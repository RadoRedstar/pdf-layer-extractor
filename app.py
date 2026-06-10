"""
app.py — Visionneuse interactive de calques PDF
Cliquez sur un élément du plan pour voir son calque OCG.

Lancement : .venv/Scripts/streamlit run app.py
"""

import time
from pathlib import Path

import numpy as np
import streamlit as st
from PIL import ImageDraw

# -------------------------------------------------------------------
# Page config (doit être le premier appel Streamlit)
# -------------------------------------------------------------------
st.set_page_config(
    page_title="Calques PDF",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------------------------------------------------------
# Imports différés (évite l'erreur si bibliothèques absentes)
# -------------------------------------------------------------------
try:
    import pypdfium2 as pdfium
    from streamlit_image_coordinates import streamlit_image_coordinates
    from layer_parser import build_index, query_index
except ImportError as e:
    st.error(f"Bibliothèque manquante : {e}")
    st.code(".venv\\Scripts\\pip install streamlit streamlit-image-coordinates pypdfium2")
    st.stop()

from PIL import Image


# -------------------------------------------------------------------
# Fonctions cachées
# -------------------------------------------------------------------

@st.cache_data(show_spinner="Analyse des calques en cours… (30–60 s pour les grands PDFs)")
def cached_build_index(pdf_path: str, page_num: int):
    return build_index(pdf_path, page_num)


@st.cache_data(show_spinner="Rendu de la page…")
def cached_render(pdf_path: str, page_num: int, scale: float) -> Image.Image:
    doc = pdfium.PdfDocument(pdf_path)
    page = doc[page_num]
    bitmap = page.render(scale=scale)
    img = bitmap.to_pil()
    doc.close()
    return img


# -------------------------------------------------------------------
# Sidebar — paramètres
# -------------------------------------------------------------------

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

with st.sidebar:
    st.title("📐 Calques PDF")
    st.markdown("---")

    uploaded_file = st.file_uploader(
        "Déposer un fichier PDF",
        type="pdf",
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
        pdf_path = UPLOAD_DIR / uploaded_file.name
        pdf_path.write_bytes(uploaded_file.getvalue())

    render_scale = st.slider("Résolution du rendu", min_value=0.5, max_value=3.0, value=1.5, step=0.25,
                             help="1.5 = bonne qualité sans trop de mémoire")
    tolerance = st.slider("Tolérance de clic (points PDF)", min_value=0, max_value=100, value=15, step=5,
                          help="Augmenter si aucun calque n'est trouvé au clic")
    pad_pts = st.slider("Zone d'inspection (pts PDF)", min_value=20, max_value=400, value=120, step=20,
                        key="patch_pad", help="Taille de la vignette autour du clic")

    st.markdown("---")
    st.markdown("**Zoom**")
    zoom = st.slider("Niveau de zoom", min_value=1, max_value=8, value=1, step=1,
                     key="zoom", help="1 = vue complète, 8 = zoom maximum")

    if zoom > 1:
        pan_x = st.slider("Position horizontale", 0, 100, 50, key="pan_x",
                          help="0 = gauche, 100 = droite")
        pan_y = st.slider("Position verticale", 0, 100, 50, key="pan_y",
                          help="0 = haut, 100 = bas")
        if st.button("Réinitialiser la vue", use_container_width=True):
            st.session_state["zoom"] = 1
            st.session_state["pan_x"] = 50
            st.session_state["pan_y"] = 50
            st.rerun()
    else:
        pan_x, pan_y = 50, 50

    st.markdown("---")
    st.markdown(
        "**Mode d'emploi**\n"
        "1. Déposer un PDF avec calques OCG\n"
        "2. Attendre la construction de l'index\n"
        "3. Zoomer si besoin, puis cliquer\n"
        "4. Voir le/les calques affichés à droite"
    )


# -------------------------------------------------------------------
# Chargement du PDF
# -------------------------------------------------------------------

if uploaded_file is None:
    st.info("Déposez un fichier PDF dans la barre latérale pour commencer.")
    st.stop()

# Nombre de pages
try:
    doc_tmp = pdfium.PdfDocument(str(pdf_path))
    n_pages = len(doc_tmp)
    doc_tmp.close()
except Exception as e:
    st.error(f"Impossible d'ouvrir le PDF : {e}")
    st.stop()

with st.sidebar:
    page_num = st.number_input("Page", min_value=1, max_value=n_pages, value=1, step=1) - 1

# -------------------------------------------------------------------
# Construction de l'index spatial
# -------------------------------------------------------------------

t0 = time.time()
with st.spinner("Chargement de l'index spatial…"):
    index = cached_build_index(str(pdf_path), page_num)
elapsed = time.time() - t0

if not index["has_ocg"]:
    st.warning(
        "Ce PDF ne contient pas de calques OCG.\n\n"
        "Pour utiliser cette application, le fichier `.dgn` MicroStation doit être "
        "réexporté avec l'option **Export PDF Layers** activée."
    )
    st.stop()

n_elements = len(index["bboxes"])
n_layers = len(index["names"])

with st.sidebar:
    st.markdown("---")
    st.success(f"Index : **{n_elements:,}** éléments • **{n_layers:,}** calques")
    if elapsed > 1:
        st.caption(f"Index construit en {elapsed:.1f} s (mis en cache)")

# -------------------------------------------------------------------
# Rendu de la page
# -------------------------------------------------------------------

img = cached_render(str(pdf_path), page_num, render_scale)
img_w, img_h = img.size
page_w = index["page_w"]
page_h = index["page_h"]


# -------------------------------------------------------------------
# Calcul de la région zoomée
# -------------------------------------------------------------------

crop_w = max(1, img_w // zoom)
crop_h = max(1, img_h // zoom)

# Centre de la vue en pixels (pan 0–100 % → 0–img_w/h)
cx_center = int(pan_x / 100.0 * img_w)
cy_center = int(pan_y / 100.0 * img_h)

crop_x0 = max(0, min(cx_center - crop_w // 2, img_w - crop_w))
crop_y0 = max(0, min(cy_center - crop_h // 2, img_h - crop_h))
crop_x1 = crop_x0 + crop_w
crop_y1 = crop_y0 + crop_h

if zoom > 1:
    cropped = img.crop((crop_x0, crop_y0, crop_x1, crop_y1))
    # Redimensionner à la largeur de l'image originale pour conserver la même UI
    display_h = int(crop_h * img_w / crop_w)
    display_img = cropped.resize((img_w, display_h), Image.LANCZOS)
else:
    display_img = img
    display_h = img_h


def pixel_to_pdf(click_x, click_y):
    """Convertit un clic dans display_img en coordonnées PDF."""
    # Pixel dans l'image originale (avant crop/resize)
    orig_px_x = crop_x0 + click_x * crop_w / img_w
    orig_px_y = crop_y0 + click_y * crop_h / display_h
    # Coordonnées PDF (Y inversé)
    x = orig_px_x / render_scale
    y = (img_h - orig_px_y) / render_scale
    return x, y


# -------------------------------------------------------------------
# Minimap (quand zoom > 1)
# -------------------------------------------------------------------

def make_layer_patch(img, index, x_pdf, y_pdf, target_layer, render_scale, pad_pts=150, thumb_w=300):
    """
    Vignette centrée sur (x_pdf, y_pdf) avec le calque cible surligné en rouge
    et les autres éléments de la zone en gris (contexte).
    La croix bleue indique le point cliqué.
    """
    rs = render_scale
    img_w, img_h = img.size
    page_w = index["page_w"]
    page_h = index["page_h"]

    px0 = max(0.0,    x_pdf - pad_pts)
    py0 = max(0.0,    y_pdf - pad_pts)
    px1 = min(page_w, x_pdf + pad_pts)
    py1 = min(page_h, y_pdf + pad_pts)

    ix0 = max(0,     int(px0 * rs))
    iy0 = max(0,     int((page_h - py1) * rs))
    ix1 = min(img_w, int(px1 * rs))
    iy1 = min(img_h, int((page_h - py0) * rs))

    if ix1 <= ix0 or iy1 <= iy0:
        return Image.new("RGB", (thumb_w, thumb_w), color=(240, 240, 240))

    patch = img.crop((ix0, iy0, ix1, iy1)).convert("RGBA")
    draw  = ImageDraw.Draw(patch, "RGBA")

    bb    = index["bboxes"]
    li    = index["layers"]
    names = index["names"]

    in_patch = (bb[:, 0] < px1) & (bb[:, 2] > px0) & (bb[:, 1] < py1) & (bb[:, 3] > py0)
    hit_idx  = np.where(in_patch)[0]

    def to_patch_px(bx0, by0, bx1, by1):
        return (
            int((bx0 - px0) * rs),
            int((py1 - by1) * rs),
            int((bx1 - px0) * rs),
            int((py1 - by0) * rs),
        )

    for i in hit_idx:
        if names[li[i]] == target_layer:
            continue
        r = to_patch_px(*bb[i])
        draw.rectangle(r, outline=(150, 150, 150, 110), width=1)

    for i in hit_idx:
        if names[li[i]] != target_layer:
            continue
        r = to_patch_px(*bb[i])
        draw.rectangle(r, fill=(220, 30, 30, 75), outline=(200, 0, 0, 255), width=2)

    cx_p = int((x_pdf - px0) * rs)
    cy_p = int((py1 - y_pdf) * rs)
    arm  = max(8, int(15 * rs))
    draw.line([(cx_p - arm, cy_p), (cx_p + arm, cy_p)], fill=(30, 80, 255, 255), width=2)
    draw.line([(cx_p, cy_p - arm), (cx_p, cy_p + arm)], fill=(30, 80, 255, 255), width=2)
    draw.ellipse([cx_p - 3, cy_p - 3, cx_p + 3, cy_p + 3], fill=(30, 80, 255, 255))

    pw, ph  = patch.size
    ratio   = thumb_w / pw if pw > 0 else 1
    thumb_h = max(1, int(ph * ratio))
    return patch.convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS)


def make_minimap(full_img, box, thumb_w=280):
    """Vignette de l'image complète avec rectangle rouge montrant la zone zoomée."""
    ratio = thumb_w / full_img.width
    thumb_h = int(full_img.height * ratio)
    thumb = full_img.resize((thumb_w, thumb_h), Image.LANCZOS)
    draw = ImageDraw.Draw(thumb)
    rx0 = int(box[0] * ratio)
    ry0 = int(box[1] * ratio)
    rx1 = int(box[2] * ratio)
    ry1 = int(box[3] * ratio)
    draw.rectangle([rx0, ry0, rx1, ry1], outline="red", width=3)
    return thumb


# -------------------------------------------------------------------
# Marqueur de clic
# -------------------------------------------------------------------

def draw_click_marker(base_img, cx, cy):
    """Dessine un repère visuel au point cliqué (cercle + croix)."""
    marked   = base_img.convert("RGBA")
    overlay  = Image.new("RGBA", marked.size, (0, 0, 0, 0))
    d        = ImageDraw.Draw(overlay)

    # Halo blanc pour contraste sur fond clair ou foncé
    d.ellipse([cx-15, cy-15, cx+15, cy+15], fill=(255, 255, 255, 140))
    # Anneau orange
    d.ellipse([cx-11, cy-11, cx+11, cy+11], fill=(255, 140, 0, 230))
    # Point central rouge foncé
    d.ellipse([cx-4,  cy-4,  cx+4,  cy+4],  fill=(180, 30,  0,  255))

    # Branches de croix (blanc semi-transparent)
    arm = 22
    for x0, y0, x1, y1 in [
        (cx-arm, cy,    cx-13, cy   ),
        (cx+13,  cy,    cx+arm, cy  ),
        (cx,     cy-arm, cx,   cy-13),
        (cx,     cy+13,  cx,   cy+arm),
    ]:
        d.line([(x0, y0), (x1, y1)], fill=(255, 255, 255, 200), width=2)

    return Image.alpha_composite(marked, overlay).convert("RGB")


# -------------------------------------------------------------------
# Interface principale
# -------------------------------------------------------------------

col_plan, col_info = st.columns([3, 1])

with col_plan:
    zoom_label = f" — zoom ×{zoom}" if zoom > 1 else ""
    st.subheader(f"Plan — {pdf_path.name} (page {page_num + 1}/{n_pages}){zoom_label}")

    # Récupérer le dernier clic connu pour afficher le marqueur dès le rerun suivant
    _prev = st.session_state.get(f"pdf_{page_num}_{render_scale}_{zoom}_{pan_x}_{pan_y}")
    if _prev:
        view_img = draw_click_marker(display_img, _prev["x"], _prev["y"])
    else:
        view_img = display_img

    click = streamlit_image_coordinates(view_img, key=f"pdf_{page_num}_{render_scale}_{zoom}_{pan_x}_{pan_y}")

    if zoom > 1:
        with st.expander("Vue d'ensemble (minimap)", expanded=True):
            minimap = make_minimap(img, (crop_x0, crop_y0, crop_x1, crop_y1))
            st.image(minimap, use_container_width=False)

with col_info:
    st.subheader("Calques détectés")

    if click is None:
        st.info("Cliquez sur le plan pour identifier un élément.")
    else:
        cx, cy = click["x"], click["y"]
        x_pdf, y_pdf = pixel_to_pdf(cx, cy)
        results = query_index(index, x_pdf, y_pdf, tolerance=float(tolerance))

        if not results:
            st.warning(
                "Aucun calque trouvé.\n\n"
                f"Augmentez la tolérance (actuellement {tolerance} pts)."
            )
        else:
            st.caption(f"{len(results)} calque(s) — PDF ({x_pdf:.0f}, {y_pdf:.0f})")
            for i, (layer_name, _area) in enumerate(results):
                st.markdown(f"**{i + 1}.**")
                st.code(layer_name, language=None)
                patch = make_layer_patch(
                    img, index, x_pdf, y_pdf, layer_name,
                    render_scale, pad_pts=pad_pts, thumb_w=400,
                )
                st.image(patch, use_container_width=True)
                st.markdown("---")

        with st.sidebar:
            st.markdown("---")
            st.markdown("**Dernier clic**")
            st.caption(f"PDF : ({x_pdf:.1f}, {y_pdf:.1f})")
            if results:
                st.caption(f"Calque principal :\n{results[0][0]}")


# -------------------------------------------------------------------
# Liste des calques disponibles (expander)
# -------------------------------------------------------------------

with st.expander(f"Tous les calques ({n_layers})", expanded=False):
    search = st.text_input("Filtrer les calques", placeholder="Ex: Luminaires, ECL, FO…")
    names_display = index["names"]
    if search:
        names_display = [n for n in names_display if search.lower() in n.lower()]
    st.caption(f"{len(names_display)} calques affichés")
    for name in names_display:
        st.text(name)
