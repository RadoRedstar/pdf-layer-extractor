"""
extract_pdf_layers.py
Usage: python extract_pdf_layers.py <fichier.pdf> [--text] [--output dossier]

Extrait les calques OCG, métadonnées, annotations et texte d'un PDF MicroStation/CAO.
Génère un fichier <nom_pdf>_layers.txt avec la liste complète des calques.
"""

import sys
import io
import re
import argparse
from pathlib import Path
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    from pypdf import PdfReader
    import pdfplumber
except ImportError:
    print("Bibliothèques manquantes. Installez-les avec :")
    print("  .venv\\Scripts\\pip install pypdf pdfplumber")
    sys.exit(1)


def parse_date(d):
    if not d:
        return ''
    s = str(d).replace('D:', '')
    try:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]} {s[8:10]}:{s[10:12]}"
    except Exception:
        return str(d)


def extract_layers(reader):
    root = reader.trailer['/Root'].get_object()
    if '/OCProperties' not in root:
        return None, None
    ocp = root['/OCProperties'].get_object()
    ocgs = ocp.get('/OCGs', [])
    d_config = ocp.get('/D', None)

    layers = []
    for ocg in ocgs:
        obj = ocg.get_object()
        name = str(obj.get('/Name', ''))
        layers.append(name)

    on_count = off_count = 0
    base_state = 'ON'
    if d_config:
        dc = d_config.get_object()
        base_state = str(dc.get('/BaseState', '/ON')).replace('/', '')
        on_count = len(dc['/ON'].get_object()) if '/ON' in dc else 0
        off_count = len(dc['/OFF'].get_object()) if '/OFF' in dc else 0

    return layers, {'base_state': base_state, 'on': on_count, 'off': off_count}


def extract_annotations(page):
    result = []
    if '/Annots' not in page:
        return result
    annots = page['/Annots'].get_object()
    for ann in annots:
        obj = ann.get_object()
        subtype = str(obj.get('/Subtype', ''))
        contents = str(obj.get('/Contents', ''))
        rect = [round(float(x), 1) for x in obj.get('/Rect', [])]
        result.append({'type': subtype, 'contents': contents, 'rect': rect})
    return result


def extract_viewports(page):
    result = []
    if '/VP' not in page:
        return result
    vp_list = page['/VP'].get_object()
    for item in vp_list:
        obj = item.get_object() if hasattr(item, 'get_object') else item
        if not hasattr(obj, 'keys'):
            continue
        bbox = [round(float(x), 2) for x in obj.get('/BBox', [])]
        measure = obj.get('/Measure', None)
        r_str = ''
        if measure and hasattr(measure, 'get_object'):
            mo = measure.get_object()
            r_str = str(mo.get('/R', ''))
        result.append({'bbox': bbox, 'scale': r_str.strip()})
    return result


def group_layers(layers):
    categories = defaultdict(list)
    for name in layers:
        if re.match(r'ASSMOJM2CFO_ECL_._Luminaires', name):
            lot = 'A' if '_ECL_A_' in name else 'B'
            cat = f'Luminaires (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_ECL_._', name):
            lot = 'A' if '_ECL_A_' in name else 'B'
            cat = f'Eclairage commande/détecteur (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_ECS_._', name):
            lot = 'A' if '_ECS_A_' in name else 'B'
            cat = f'Eclairage de Secours (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_FO_._', name):
            lot = 'A' if '_FO_A_' in name else 'B'
            cat = f'Force/Alimentation (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_BSO_._', name):
            lot = 'A' if '_BSO_A_' in name else 'B'
            cat = f'Boîtiers de sol (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_CC_._', name):
            lot = 'A' if '_CC_A_' in name else 'B'
            cat = f'Chemins de câbles (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_PRS_._', name):
            lot = 'A' if '_PRS_A_' in name else 'B'
            cat = f'Prises (lot {lot})'
        elif re.match(r'ASSMOJM2CFO_TS_', name):
            cat = 'Tableaux/Tableautins'
        elif re.match(r'ASSMOJM2CFO_TXT_', name):
            cat = 'Textes/Alimentations'
        elif re.match(r'ASSMOJM2CFO_', name):
            cat = 'ASSMOJM2CFO - Autres'
        elif re.match(r'ASSMO_JM2CFO_', name):
            cat = 'Zones ASSMO'
        elif re.match(r'EL-\d+', name):
            cat = 'EL - Niveaux électriques'
        elif re.match(r'_LAS_', name):
            cat = '_LAS - Référence architecturale'
        elif re.match(r'A_|A__', name):
            cat = 'A_ - Blocs Architecture'
        elif re.match(r'00-', name):
            cat = '00 - Légende / Modifications'
        elif 'Légende' in name or 'Legende' in name or 'LEG' in name:
            cat = 'Légende'
        elif name.startswith('Remarque') or name.startswith('remarque'):
            cat = 'Remarques'
        elif name.startswith('Borne'):
            cat = 'Bornes'
        elif name.startswith('_Chemin') or name.startswith('_Coupe') or name.startswith('_Fourreau'):
            cat = 'Infrastructure câbles (_)'
        elif any(x in name for x in ['PLO', 'IE - RDC', 'AR - RDC', '.dgn<']):
            cat = 'Fichiers DGN référencés'
        elif name in ['Références', 'Niveaux', 'Par défaut', 'FR']:
            cat = 'Système / Référence'
        else:
            cat = 'Autres'
        categories[cat].append(name)
    return categories


def main():
    parser = argparse.ArgumentParser(description='Extrait les calques OCG et infos d\'un PDF CAO')
    parser.add_argument('pdf', help='Chemin vers le fichier PDF')
    parser.add_argument('--text', action='store_true', help='Extraire aussi le texte complet')
    parser.add_argument('--output', default=None, help='Dossier de sortie (défaut: même dossier que le PDF)')
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Erreur: fichier introuvable: {pdf_path}")
        sys.exit(1)

    out_dir = Path(args.output) if args.output else pdf_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    layers_file = out_dir / (pdf_path.stem + '_layers.txt')

    print(f"Analyse de: {pdf_path.name}")
    print("=" * 60)

    reader = PdfReader(str(pdf_path))

    # --- Métadonnées ---
    meta = reader.metadata or {}
    print("\n[MÉTADONNÉES]")
    print(f"  Créateur  : {meta.get('/Creator', 'N/A')}")
    print(f"  Créé le   : {parse_date(meta.get('/CreationDate', ''))}")
    print(f"  Modifié le: {parse_date(meta.get('/ModDate', ''))}")
    print(f"  Producteur: {meta.get('/Producer', 'N/A')}")
    print(f"  Pages     : {len(reader.pages)}")

    # --- Calques OCG ---
    print("\n[CALQUES OCG]")
    layers, layer_config = extract_layers(reader)
    if layers is None:
        print("  Aucun calque OCG dans ce PDF (non exporté depuis MicroStation).")
        print("  Le fichier source .dgn doit être réexporté avec l'option 'Export PDF Layers'.")
    else:
        print(f"  Nombre de calques : {len(layers)}")
        print(f"  État par défaut   : {layer_config['base_state']}")
        if layer_config['on']:
            print(f"  Calques ON        : {layer_config['on']}")
        if layer_config['off']:
            print(f"  Calques OFF       : {layer_config['off']}")

        # Grouper par catégorie
        categories = group_layers(layers)
        print("\n  Résumé par catégorie:")
        for cat, names in sorted(categories.items(), key=lambda x: -len(x[1])):
            print(f"  [{len(names):4d}] {cat}")

        # Exporter liste complète
        with open(layers_file, 'w', encoding='utf-8') as f:
            f.write(f"CALQUES OCG - {pdf_path.name}\n")
            f.write(f"Total: {len(layers)} calques\n")
            f.write("=" * 60 + "\n\n")
            f.write("--- PAR NUMÉRO ---\n")
            for i, name in enumerate(layers):
                f.write(f"{i+1:04d}: {name}\n")
            f.write("\n--- PAR CATÉGORIE ---\n")
            for cat, names in sorted(categories.items(), key=lambda x: -len(x[1])):
                f.write(f"\n[{len(names)}] {cat}\n")
                for name in names:
                    f.write(f"  {name}\n")
        print(f"\n  Liste complète exportée : {layers_file}")

    # --- Par page ---
    for page_num, page in enumerate(reader.pages):
        print(f"\n[PAGE {page_num + 1}]")

        # Viewports
        vps = extract_viewports(page)
        if vps:
            print(f"  Viewports ({len(vps)}):")
            for i, vp in enumerate(vps):
                print(f"    VP[{i}] {vp['scale']}")

        # Annotations
        annots = extract_annotations(page)
        if annots:
            print(f"\n  Annotations ({len(annots)}):")
            for ann in annots:
                preview = ann['contents'][:100].replace('\n', ' / ')
                print(f"    [{ann['type']}] {preview}")
        else:
            print("  Aucune annotation.")

    # --- Texte ---
    if args.text:
        print("\n[TEXTE EXTRAIT]")
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, p in enumerate(pdf.pages):
                text = p.extract_text()
                if text:
                    print(f"\n--- Page {i+1} ---")
                    print(text)
                else:
                    print(f"  Page {i+1}: aucun texte extractible.")

    print("\nTerminé.")


if __name__ == '__main__':
    main()
