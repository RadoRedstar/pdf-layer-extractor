"""
layer_parser.py
Parse le flux de contenu d'une page PDF et construit un index spatial :
  bbox (x0,y0,x1,y1) → nom du calque OCG

Fonctionne uniquement sur les PDFs avec calques OCG (export MicroStation avec layers).
"""

import numpy as np
from pypdf import PdfReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _num(ops, i, default=0.0):
    if i < len(ops) and ops[i][0] == 'num':
        return ops[i][1]
    return default


def _name(ops, i, default=''):
    if i < len(ops) and ops[i][0] == 'name':
        return ops[i][1]
    return default


def _make_ctm(a, b, c, d, e, f):
    """Matrice 3x3 pour la transformation affine PDF.
    PDF: x' = a*x + c*y + e,  y' = b*x + d*y + f
    """
    return np.array([[a, c, e],
                     [b, d, f],
                     [0, 0, 1]], dtype=float)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def _tokenize(data: bytes):
    """
    Générateur qui tokenize un flux de contenu PDF.
    Produit des tuples (type, valeur) :
      'num'    → float
      'name'   → str  (commence par /)
      'str'    → None (chaîne littérale, contenu ignoré)
      'array'  → None (tableau, contenu ignoré)
      'dict'   → None (dictionnaire inline, contenu ignoré)
      'op'     → str  (opérateur PDF)
    """
    i = 0
    n = len(data)
    while i < n:
        # Espaces blancs
        while i < n and data[i:i+1] in (b' ', b'\t', b'\r', b'\n', b'\x00'):
            i += 1
        if i >= n:
            break
        c = data[i]

        # Commentaire
        if c == ord('%'):
            while i < n and data[i:i+1] not in (b'\r', b'\n'):
                i += 1
            continue

        # Chaîne littérale  (...)
        if c == ord('('):
            depth = 1
            i += 1
            while i < n and depth > 0:
                ch = data[i]
                if ch == ord('\\'):
                    i += 2
                elif ch == ord('('):
                    depth += 1; i += 1
                elif ch == ord(')'):
                    depth -= 1; i += 1
                else:
                    i += 1
            yield ('str', None)
            continue

        # Dictionnaire <<...>>  ou chaîne hex <...>
        if c == ord('<'):
            if i + 1 < n and data[i+1] == ord('<'):
                depth = 1; i += 2
                while i < n - 1 and depth > 0:
                    if data[i:i+2] == b'<<':
                        depth += 1; i += 2
                    elif data[i:i+2] == b'>>':
                        depth -= 1; i += 2
                    else:
                        i += 1
                yield ('dict', None)
            else:
                end = data.find(b'>', i + 1)
                if end == -1:
                    break
                yield ('str', None)
                i = end + 1
            continue

        # Tableau [...]
        if c == ord('['):
            depth = 1; i += 1
            while i < n and depth > 0:
                ch = data[i]
                if ch == ord('['):
                    depth += 1; i += 1
                elif ch == ord(']'):
                    depth -= 1; i += 1
                elif ch == ord('('):
                    d2 = 1; i += 1
                    while i < n and d2 > 0:
                        if data[i] == ord('\\'): i += 2; continue
                        if data[i] == ord('('): d2 += 1
                        elif data[i] == ord(')'): d2 -= 1
                        i += 1
                else:
                    i += 1
            yield ('array', None)
            continue

        # Nom  /xxx
        if c == ord('/'):
            end = i + 1
            while end < n and data[end] not in b' \t\r\n\x00/<>()[]{}':
                end += 1
            try:
                yield ('name', data[i:end].decode('latin-1'))
            except Exception:
                yield ('name', '')
            i = end
            continue

        # Nombre ou opérateur
        end = i
        while end < n and data[end] not in b' \t\r\n\x00/<>()[]{}':
            end += 1
        token = data[i:end]
        i = end
        if not token:
            i += 1
            continue
        try:
            yield ('num', float(token))
        except (ValueError, UnicodeDecodeError):
            try:
                yield ('op', token.decode('latin-1'))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_index(pdf_path: str, page_num: int = 0, progress_cb=None):
    """
    Construit l'index spatial d'une page PDF.

    Retourne un dict :
      'bboxes'  : np.float32 (N, 4) — x0, y0, x1, y1 en coordonnées PDF
      'layers'  : np.int32   (N,)   — index dans 'names'
      'names'   : list[str]         — noms des calques uniques
      'page_w'  : float             — largeur page (points PDF)
      'page_h'  : float             — hauteur page (points PDF)
      'has_ocg' : bool              — True si des calques OCG ont été trouvés
    """
    reader = PdfReader(str(pdf_path))
    page = reader.pages[page_num]

    # Dimensions de la page
    mb = page.mediabox
    page_w = float(mb.width)
    page_h = float(mb.height)

    # /Properties  :  '/MC42' → 'nom du calque'
    resources = page.get('/Resources', {})
    if hasattr(resources, 'get_object'):
        resources = resources.get_object()

    props = {}
    if '/Properties' in resources:
        p = resources['/Properties'].get_object()
        for k in p.keys():
            try:
                ocg = p[k].get_object()
                props[k] = str(ocg.get('/Name', ''))
            except Exception:
                pass

    has_ocg = bool(props)

    # Flux de contenu
    if '/Contents' not in page:
        return _empty(page_w, page_h)

    contents = page['/Contents'].get_object()
    if hasattr(contents, 'get_data'):
        raw = contents.get_data()
    else:
        raw = b''
        for item in contents:
            try:
                raw += item.get_object().get_data()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Parcours du flux
    # ------------------------------------------------------------------
    elements = []          # liste de (x0, y0, x1, y1, layer_name)

    ctm_stack  = [np.eye(3, dtype=float)]  # matrices CTM empilées
    ocg_stack  = [None]                    # noms de calque empilés
    operands   = []

    # état du chemin courant
    path_xs, path_ys = [], []
    cur_x = cur_y = 0.0

    # état texte
    in_text     = False
    text_matrix = np.eye(3, dtype=float)
    text_line   = np.eye(3, dtype=float)
    font_size   = 12.0

    def get_layer():
        for name in reversed(ocg_stack):
            if name:
                return name
        return None

    def xform_pts(xs, ys):
        if not xs:
            return None
        ctm = ctm_stack[-1]
        pts = np.array([xs, ys, [1.0] * len(xs)])
        t = ctm @ pts
        return float(t[0].min()), float(t[1].min()), float(t[0].max()), float(t[1].max())

    def save_path():
        nonlocal path_xs, path_ys
        if path_xs:
            layer = get_layer()
            if layer:
                bbox = xform_pts(path_xs, path_ys)
                if bbox and (bbox[2] - bbox[0] > 0.1 or bbox[3] - bbox[1] > 0.1):
                    elements.append(bbox + (layer,))
        path_xs, path_ys = [], []

    for tok_type, tok_val in _tokenize(raw):
        if tok_type != 'op':
            operands.append((tok_type, tok_val))
            continue

        op  = tok_val
        ops = operands
        operands = []

        # --- État graphique ---
        if op == 'q':
            ctm_stack.append(ctm_stack[-1].copy())
            ocg_stack.append(ocg_stack[-1])

        elif op == 'Q':
            if len(ctm_stack) > 1: ctm_stack.pop()
            if len(ocg_stack) > 1: ocg_stack.pop()
            path_xs, path_ys = [], []

        elif op == 'cm':
            if len(ops) >= 6:
                m = _make_ctm(_num(ops,0), _num(ops,1), _num(ops,2),
                               _num(ops,3), _num(ops,4), _num(ops,5))
                ctm_stack[-1] = ctm_stack[-1] @ m

        # --- Calques OCG ---
        elif op == 'BDC':
            layer = ocg_stack[-1]
            if len(ops) >= 2:
                mc_key = _name(ops, 1)
                if mc_key in props:
                    layer = props[mc_key]
            ocg_stack.append(layer)

        elif op in ('BMC', 'DP', 'MP'):
            ocg_stack.append(ocg_stack[-1])

        elif op == 'EMC':
            if len(ocg_stack) > 1: ocg_stack.pop()

        # --- Construction du chemin ---
        elif op == 'm':
            cur_x, cur_y = _num(ops,0), _num(ops,1)
            path_xs, path_ys = [cur_x], [cur_y]

        elif op == 'l':
            cur_x, cur_y = _num(ops,0), _num(ops,1)
            path_xs.append(cur_x); path_ys.append(cur_y)

        elif op == 'c':
            path_xs += [_num(ops,0), _num(ops,2), _num(ops,4)]
            path_ys += [_num(ops,1), _num(ops,3), _num(ops,5)]
            cur_x, cur_y = _num(ops,4), _num(ops,5)

        elif op == 'v':
            path_xs += [cur_x, _num(ops,0), _num(ops,2)]
            path_ys += [cur_y, _num(ops,1), _num(ops,3)]
            cur_x, cur_y = _num(ops,2), _num(ops,3)

        elif op == 'y':
            path_xs += [_num(ops,0), _num(ops,2), _num(ops,2)]
            path_ys += [_num(ops,1), _num(ops,3), _num(ops,3)]
            cur_x, cur_y = _num(ops,2), _num(ops,3)

        elif op == 're':
            x, y, w, h = _num(ops,0), _num(ops,1), _num(ops,2), _num(ops,3)
            path_xs = [x, x+w, x+w, x]
            path_ys = [y, y,   y+h, y+h]

        elif op == 'h':
            pass  # closepath — pas de nouveaux points

        # --- Tracé du chemin ---
        elif op in ('S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*'):
            save_path()

        elif op == 'n':
            path_xs, path_ys = [], []   # clipping — ne pas enregistrer

        # --- Texte ---
        elif op == 'BT':
            in_text = True
            text_matrix = np.eye(3, dtype=float)
            text_line   = np.eye(3, dtype=float)

        elif op == 'ET':
            in_text = False

        elif op == 'Tf' and in_text:
            font_size = abs(_num(ops, 1, 12.0)) or 12.0

        elif op == 'Tm' and in_text:
            if len(ops) >= 6:
                text_matrix = _make_ctm(*[_num(ops, i) for i in range(6)])
                text_line   = text_matrix.copy()

        elif op in ('Td', 'TD') and in_text:
            if len(ops) >= 2:
                t = _make_ctm(1, 0, 0, 1, _num(ops,0), _num(ops,1))
                text_line   = text_line @ t
                text_matrix = text_line.copy()

        elif op in ('Tj', 'TJ', "'", '"') and in_text:
            layer = get_layer()
            if layer:
                combined = ctm_stack[-1] @ text_matrix
                tx, ty = combined[0, 2], combined[1, 2]
                fs = font_size * max(abs(combined[0, 0]), abs(combined[1, 1]), 0.5)
                elements.append((tx, ty, tx + fs * 4, ty + fs, layer))

        # --- XObjects (images, formes) ---
        elif op == 'Do':
            layer = get_layer()
            if layer:
                ctm = ctm_stack[-1]
                corners = np.array([[0,0,1],[1,0,1],[1,1,1],[0,1,1]], dtype=float).T
                t = ctm @ corners
                x0, y0, x1, y1 = t[0].min(), t[1].min(), t[0].max(), t[1].max()
                if x1 - x0 > 0.1 or y1 - y0 > 0.1:
                    elements.append((float(x0), float(y0), float(x1), float(y1), layer))

    # ------------------------------------------------------------------
    # Construction des tableaux numpy
    # ------------------------------------------------------------------
    if not elements:
        return _empty(page_w, page_h, has_ocg)

    name_to_idx = {}
    names = []
    layer_idx_list = []
    bboxes_list = []

    for x0, y0, x1, y1, name in elements:
        if name not in name_to_idx:
            name_to_idx[name] = len(names)
            names.append(name)
        layer_idx_list.append(name_to_idx[name])
        bboxes_list.append([x0, y0, x1, y1])

    return {
        'bboxes':  np.array(bboxes_list, dtype=np.float32),
        'layers':  np.array(layer_idx_list, dtype=np.int32),
        'names':   names,
        'page_w':  page_w,
        'page_h':  page_h,
        'has_ocg': has_ocg,
    }


def _empty(page_w, page_h, has_ocg=False):
    return {
        'bboxes':  np.empty((0, 4), dtype=np.float32),
        'layers':  np.empty(0, dtype=np.int32),
        'names':   [],
        'page_w':  page_w,
        'page_h':  page_h,
        'has_ocg': has_ocg,
    }


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_index(index, x_pdf, y_pdf, tolerance=15.0):
    """
    Trouve tous les calques dont la bbox contient le point (x_pdf, y_pdf).
    Retourne une liste de (nom_calque, aire) triée par aire croissante
    (le calque le plus petit/précis en premier).
    """
    bb = index['bboxes']
    if len(bb) == 0:
        return []

    tol = tolerance
    mask = ((bb[:, 0] - tol) <= x_pdf) & (x_pdf <= (bb[:, 2] + tol)) & \
           ((bb[:, 1] - tol) <= y_pdf) & (y_pdf <= (bb[:, 3] + tol))

    hit_idx = np.where(mask)[0]
    if len(hit_idx) == 0:
        return []

    hit_bb = bb[hit_idx]
    areas  = (hit_bb[:, 2] - hit_bb[:, 0]) * (hit_bb[:, 3] - hit_bb[:, 1])
    order  = np.argsort(areas)

    seen, results = set(), []
    for pos in order:
        i    = hit_idx[pos]
        name = index['names'][index['layers'][i]]
        if name and name not in seen:
            seen.add(name)
            results.append((name, float(areas[pos])))
        if len(results) >= 10:
            break

    return results
