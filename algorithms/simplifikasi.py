# -*- coding: utf-8 -*-
# =============================================================================
# SIMPLIFIKASI - Generalisasi Garis Pantai
# =============================================================================
# Toolbox : Generalisasi Garis Pantai
# Tahap   : 6 - Simplifikasi
# Input   : Layer garis pantai (EPSG:3857 atau proyeksi meter)
#           Skala input dan skala target
#           Kolom morfologi
# Output  : Layer garis pantai tersimplifikasi dengan kolom tambahan:
#             - pred_cls   : kelas morfologi yang dibaca
#             - tol_meter  : toleransi efektif yang digunakan (m)
#             - orig_pts   : jumlah titik sebelum simplifikasi
#             - simp_pts   : jumlah titik setelah simplifikasi
#             - reduksi_pc : persentase reduksi titik (%)
#             - jml_bend   : jumlah bend terdeteksi
#             - jml_c1     : jumlah bend C1 (dipertahankan)
#             - jml_c2     : jumlah bend C2 (dihapus)
#             - stat_simp  : Tersimplifikasi / Tetap / Diabaikan
#
# Metode: Bend Simplification (Yang et al. 2018, PLOS ONE)
#   Toleransi dasar: R = 0.2mm x S (standar akurasi grafis ICA/CEN)
#   Toleransi per morfologi (McMaster & Shea 1992):
#     Orthogonal  → SKIP (geometri dipertahankan utuh)
#     Rugged      → tol x 0.45  (detail khas terbanyak, lindungi ketat)
#     Elongated   → tol x 0.60  (detail lebar kritis)
#     Broad       → tol x 0.80  (hanya kontur besar)
#     Smooth      → tol x 1.00  (kelas rata-rata, toleransi penuh)
#
# Pengecualian Tambahan:
#   Jika kolom 'gri' = 1, fitur akan di-SKIP.
#   Jika fitur < 5 titik, dipertahankan utuh otomatis (tidak disimplifikasi).
# =============================================================================

import math
import re
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterField,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsFeatureSink,
    QgsFeature,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsLineString,
    QgsWkbTypes,
    QgsFields,
    QgsField,
)

# =============================================================================
#  TABEL SKALA DAN TOLERANSI DASAR
# =============================================================================

INPUT_SCALES = [
    ('1:5.000',     5_000),
    ('1:25.000',    25_000),
    ('1:50.000',    50_000),
    ('1:250.000',   250_000),
    ('1:500.000',   500_000),
]

OUTPUT_SCALES = [
    ('1:25.000',    25_000,    50.0),
    ('1:50.000',    50_000,   100.0),
    ('1:250.000',   250_000,  500.0),
    ('1:500.000',   500_000,  1_000.0),
    ('1:1.000.000', 1_000_000, 2_000.0),
]

IN_LABELS  = [x[0] for x in INPUT_SCALES]
IN_DENOMS  = [x[1] for x in INPUT_SCALES]
OUT_LABELS = [x[0] for x in OUTPUT_SCALES]
OUT_DENOMS = [x[1] for x in OUTPUT_SCALES]
OUT_TOLS   = [x[2] for x in OUTPUT_SCALES]

# =============================================================================
#  KONSTANTA MORFOLOGI
# =============================================================================

VALID_MORPH = {'orthogonal', 'rugged', 'elongated', 'broad', 'smooth'}

MORPH_TOL_FACTOR = {
    # McMaster & Shea (1992): semakin kompleks/khas bentuk morfologi,
    # semakin kecil toleransi agar karakter kartografis terjaga.
    # tol dasar (1.0x) = 0.2mm x S (standar akurasi grafis ICA/CEN).
    'orthogonal': None,   # SKIP — bentuk buatan, sudut tegak lurus harus utuh
    'rugged':     0.45,   # paling banyak detail khas, lindungi ketat
    'elongated':  0.60,   # detail lebar kritis (spit/tombolo bisa hilang)
    'broad':      0.80,   # hanya kontur besar yang perlu dijaga
    'smooth':     1.00,   # kelas rata-rata, toleransi dasar sudah tepat
}

MAX_SEG_FACTOR = 3.0   # was 6.0 — segmen > 3x tol disisipi titik asli kembali

# =============================================================================
#  GEOMETRI DASAR
# =============================================================================

def _dist(ax, ay, bx, by):
    return math.hypot(bx - ax, by - ay)

def _perp(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    d = math.hypot(dx, dy)
    if d < 1e-14:
        return math.hypot(px - ax, py - ay)
    return abs(dy * px - dx * py + bx * ay - by * ax) / d

def _shoelace(pts):
    ring = pts + [pts[0]]
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1]
        s -= ring[i + 1][0] * ring[i][1]
    return abs(s) / 2.0

# =============================================================================
#  DETEKSI DAN KLASIFIKASI BEND
# =============================================================================

def _detect_bends(pts):
    n = len(pts)
    if n < 3:
        return [pts]
    signs = []
    for i in range(1, n - 1):
        ax = pts[i][0] - pts[i - 1][0]
        ay = pts[i][1] - pts[i - 1][1]
        bx = pts[i + 1][0] - pts[i][0]
        by = pts[i + 1][1] - pts[i][1]
        cross = ax * by - ay * bx
        signs.append(1 if cross >= 0 else -1)
    inflection_idx = [0]
    for i in range(1, len(signs)):
        if signs[i] != signs[i - 1]:
            inflection_idx.append(i + 1)
    inflection_idx.append(n - 1)
    bends = []
    for k in range(len(inflection_idx) - 1):
        seg = pts[inflection_idx[k]: inflection_idx[k + 1] + 1]
        if len(seg) >= 2:
            bends.append(seg)
    return bends if bends else [pts]

def _bend_width(bend_pts):
    return _dist(bend_pts[0][0], bend_pts[0][1], bend_pts[-1][0], bend_pts[-1][1])

def _bend_length(bend_pts):
    w = _bend_width(bend_pts)
    if w < 1e-9:
        return 0.0
    return _shoelace(bend_pts) / w

def _classify_bends(bends, tol):
    result = []
    for b in bends:
        w = _bend_width(b)
        l = _bend_length(b)
        label = 'C1' if (w >= tol or l >= tol) else 'C2'
        result.append((b, label))
    return result

def _simplify_selektif(classified_bends):
    segments = []
    for bend_pts, label in classified_bends:
        segments.append(bend_pts if label == 'C1' else [bend_pts[0], bend_pts[-1]])
    result = []
    for i, seg in enumerate(segments):
        if i == 0:
            result.extend(seg)
        else:
            if result and _dist(result[-1][0], result[-1][1], seg[0][0], seg[0][1]) < 1e-6:
                result.extend(seg[1:])
            else:
                result.extend(seg)
    cleaned = []
    for p in result:
        if not cleaned or _dist(cleaned[-1][0], cleaned[-1][1], p[0], p[1]) > 1e-6:
            cleaned.append(p)
    return cleaned

def _fix_long_segments(idx_list, pts, max_seg):
    result  = list(idx_list)
    changed = True
    it      = 0
    while changed and it < 50:
        changed = False
        new_r   = [result[0]]
        it     += 1
        for k in range(len(result) - 1):
            ia = result[k]
            ib = result[k + 1]
            ax, ay = pts[ia][0], pts[ia][1]
            bx, by = pts[ib][0], pts[ib][1]
            new_r.append(ib)
            if _dist(ax, ay, bx, by) <= max_seg or ib - ia <= 1:
                continue
            best_d, best_j = 0.0, -1
            for j in range(ia + 1, ib):
                d = _perp(pts[j][0], pts[j][1], ax, ay, bx, by)
                if d > best_d:
                    best_d, best_j = d, j
            if best_j > 0:
                new_r.insert(len(new_r) - 1, best_j)
                changed = True
        result = new_r
    return result

def _fix_self_intersections(pts_xy, orig_xy):
    try:
        qp   = [QgsPoint(p[0], p[1]) for p in pts_xy]
        geom = QgsGeometry(QgsLineString(qp))
        if geom.isSimple():
            return pts_xy
        fixed = geom.makeValid()
        if fixed and not fixed.isEmpty():
            if fixed.isMultipart():
                parts = fixed.asMultiPolyline()
                if parts:
                    longest = max(parts, key=len)
                    return [(p.x(), p.y()) if hasattr(p, 'x') else p for p in longest]
            else:
                line = fixed.asPolyline()
                if len(line) >= 2:
                    return [(p.x(), p.y()) if hasattr(p, 'x') else p for p in line]
    except Exception:
        pass
    return list(orig_xy)

def simplify_line(pts_xy, tol, max_seg):
    n = len(pts_xy)
    if n < 3:
        return list(pts_xy), 0, 0, 0
    bends      = _detect_bends(pts_xy)
    classified = _classify_bends(bends, tol)
    if all(label == 'C2' for _, label in classified):
        if n < 10:
            simp_xy = _simplify_selektif(classified)
            simp_xy = simp_xy if len(simp_xy) >= 2 else [pts_xy[0], pts_xy[-1]]
            return simp_xy, len(bends), 0, len(bends)
        else:
            classified = _classify_bends(bends, tol * 0.25)
            if all(label == 'C2' for _, label in classified):
                simp_xy = _simplify_selektif(classified)
                simp_xy = simp_xy if len(simp_xy) >= 2 else [pts_xy[0], pts_xy[-1]]
                return simp_xy, len(bends), 0, len(bends)
    jml_bend = len(classified)
    jml_c1   = sum(1 for _, label in classified if label == 'C1')
    jml_c2   = sum(1 for _, label in classified if label == 'C2')
    simp_xy  = _simplify_selektif(classified)
    if len(simp_xy) < 2:
        return list(pts_xy), jml_bend, jml_c1, jml_c2
    pt_to_idx = {}
    for i, p in enumerate(pts_xy):
        key = (round(p[0], 8), round(p[1], 8))
        if key not in pt_to_idx:
            pt_to_idx[key] = i
    idx_list = [pt_to_idx[(round(p[0], 8), round(p[1], 8))]
                for p in simp_xy
                if (round(p[0], 8), round(p[1], 8)) in pt_to_idx]
    if len(idx_list) >= 2:
        idx_fixed = _fix_long_segments(idx_list, pts_xy, max_seg)
        result    = [pts_xy[i] for i in idx_fixed]
    else:
        result = simp_xy
    result = result if len(result) >= 2 else list(pts_xy)
    return result, jml_bend, jml_c1, jml_c2

def _clean(val):
    return re.sub(r'[^a-z]', '', str(val).lower()) if val is not None else ''

def _get_morph_class(feat, field_name):
    if field_name:
        try:
            val = feat[field_name]
            c = _clean(val)
            if c in VALID_MORPH:
                return c
        except KeyError:
            pass
    for val in feat.attributes():
        c = _clean(val)
        if c in VALID_MORPH:
            return c
    return 'tidak_ditemukan'

def _resolve_tolerance(morph_class, tol_base_crs):
    if morph_class == 'orthogonal':
        return None
    factor = MORPH_TOL_FACTOR.get(morph_class, 1.0)
    return tol_base_crs * factor

def _build_output_fields(src_fields):
    new_field_defs = [
        ('pred_cls',   QVariant.String),
        ('tol_meter',  QVariant.Double),
        ('orig_pts',   QVariant.Int),
        ('simp_pts',   QVariant.Int),
        ('reduksi_pc', QVariant.Double),
        ('jml_bend',   QVariant.Int),
        ('jml_c1',     QVariant.Int),
        ('jml_c2',     QVariant.Int),
        ('stat_simp',  QVariant.String),
    ]
    new_names = {x[0].lower() for x in new_field_defs}
    f = QgsFields()
    keep_indices = []
    for i in range(src_fields.count()):
        field = src_fields.at(i)
        if field.name().lower() not in new_names:
            f.append(field)
            keep_indices.append(i)
    for name, typ in new_field_defs:
        f.append(QgsField(name, typ))
    return f, keep_indices

# =============================================================================
#  ALGORITMA UTAMA
# =============================================================================

class Simplifikasi(QgsProcessingAlgorithm):

    INPUT          = 'INPUT'
    SKALA_INPUT    = 'SKALA_INPUT'
    SKALA_TARGET   = 'SKALA_TARGET'
    PRED_CLASS_FLD = 'PRED_CLASS_FLD'
    RESOLVE_TOPO   = 'RESOLVE_TOPO'
    OUTPUT         = 'OUTPUT'

    def name(self):
        return 'simplifikasi'

    def displayName(self):
        return 'Tahap 6 - Simplifikasi'

    def group(self):
        return 'Generalisasi Garis Pantai'

    def groupId(self):
        return 'generalisasi_garis_pantai'

    def createInstance(self):
        return Simplifikasi()

    def shortHelpString(self):
        return (
            '<b>Tahap 6 - Simplifikasi</b><br>'
            '<i>Bend Simplification Adaptif — Generalisasi Garis Pantai</i><br><br>'
            'Menyederhanakan geometri garis pantai menggunakan metode '
            '<b>Bend Simplification</b> berbasis Yang et al. (2018) PLOS ONE.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: layer dalam CRS proyeksi meter<br>'
            '- <i>Skala Input & Target</i>: skala sumber & hasil generalisasi<br>'
            '- <i>Kolom Morfologi</i>: kolom berisi kelas morfologi garis pantai<br><br>'
            '<b>Faktor toleransi per morfologi (McMaster &amp; Shea 1992):</b><br>'
            '- Orthogonal → SKIP (tidak diubah)<br>'
            '- Rugged → tol &times; 0.45<br>'
            '- Elongated → tol &times; 0.60<br>'
            '- Broad → tol &times; 0.80<br>'
            '- Smooth → tol &times; 1.00<br><br>'
            '<b>Pengecualian:</b><br>'
            'Jika terdeteksi atribut `gri` bernilai 1, fitur tersebut akan dilewati '
            'tanpa proses simplifikasi.<br><br>'
            '<b>Catatan:</b><br>'
            'Untuk hasil terbaik gunakan CRS proyeksi meter (EPSG:3857). '
            'Lanjutkan ke Tahap 7 - Smoothing.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT,
            'Layer Garis Pantai (dalam CRS proyeksi meter)',
            [QgsProcessing.TypeVectorLine],
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_INPUT,
            'Skala Input (skala sumber data)',
            options=IN_LABELS,
            defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_TARGET,
            'Skala Target (skala hasil generalisasi)',
            options=OUT_LABELS,
            defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterField(
            self.PRED_CLASS_FLD,
            'Kolom Morfologi (pred_class)',
            defaultValue='pred_class',
            parentLayerParameterName=self.INPUT,
            optional=True,
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.RESOLVE_TOPO,
            'Perbaiki Self-Intersection Otomatis (disarankan)',
            defaultValue=True,
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            'Output - Simplifikasi',
        ))

    def processAlgorithm(self, parameters, context, feedback):
        source       = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        in_idx       = self.parameterAsEnum(parameters, self.SKALA_INPUT, context)
        out_idx      = self.parameterAsEnum(parameters, self.SKALA_TARGET, context)
        pred_field   = self.parameterAsString(parameters, self.PRED_CLASS_FLD, context)
        resolve_topo = self.parameterAsBool(parameters, self.RESOLVE_TOPO, context)

        if source is None:
            raise QgsProcessingException('Layer input tidak valid atau tidak terbaca.')

        in_denom  = IN_DENOMS[in_idx]
        out_denom = OUT_DENOMS[out_idx]
        tol_m     = OUT_TOLS[out_idx]

        if out_denom <= in_denom:
            raise QgsProcessingException(
                f'ERROR: Skala target ({OUT_LABELS[out_idx]}) harus lebih kecil dari '
                f'skala input ({IN_LABELS[in_idx]}). '
            )

        crs    = source.crs()
        is_geo = crs.isGeographic()

        if is_geo:
            def _to_crs(m): return m / 111_320.0
            feedback.reportError(
                'PERINGATAN: CRS layer dalam satuan derajat.\n'
                'Disarankan: reproyeksi ke EPSG:3857 sebelum simplifikasi.',
                fatalError=False,
            )
        else:
            def _to_crs(m): return m

        tol_base_crs = _to_crs(tol_m)

        out_fields, keep_indices = _build_output_fields(source.fields())
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, QgsWkbTypes.LineString, crs,
        )
        if sink is None:
            raise QgsProcessingException('Gagal membuat layer output.')

        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 6 - SIMPLIFIKASI | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Skala input      : {IN_LABELS[in_idx]}')
        feedback.pushInfo(f'Skala target     : {OUT_LABELS[out_idx]}')
        feedback.pushInfo(f'Toleransi dasar  : {tol_m:.1f} m')
        feedback.pushInfo(f'CRS              : {crs.authid()}')
        feedback.pushInfo('=' * 60)

        features  = list(source.getFeatures())
        total     = len(features)
        processed = 0

        # Inisialisasi Counter
        cnt_skip_morph = cnt_skip_gri = cnt_simp = cnt_same = cnt_pendek = cnt_fallback = cnt_retry = cnt_terlalu_kecil = 0
        s_orig = s_simp = s_bend = s_c1 = s_c2 = 0

        for feat in features:
            if feedback.isCanceled():
                break

            geom = feat.geometry()
            if not geom or geom.isEmpty():
                processed += 1
                continue

            feat_id = feat.id()
            morph_class = _get_morph_class(feat, pred_field)
            tol_eff_crs = _resolve_tolerance(morph_class, tol_base_crs)

            # =================================================================
            # CEK KONDISI PENGECUALIAN (SKIP)
            # =================================================================
            is_morph_skip = (tol_eff_crs is None)
            is_gri_skip   = False

            # Mendeteksi apakah kolom 'gri' ada dan bernilai 1
            for field_name in feat.fields().names():
                if field_name.lower() == 'gri':
                    val = feat[field_name]
                    if val is not None and str(val).strip() in ['1', '1.0']:
                        is_gri_skip = True
                    break

            is_skipped = is_morph_skip or is_gri_skip

            raw_lines = (geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()])

            out_geom  = geom
            feat_orig = sum(len(raw) for raw in raw_lines)
            feat_simp = feat_orig
            feat_bend = feat_c1 = feat_c2 = 0

            is_fallback = False
            is_retried  = False
            is_terlalu_kecil = False

            if is_skipped:
                tol_eff_m = 0.0
            else:
                tol_eff_m = tol_eff_crs if not is_geo else tol_eff_crs * 111_320.0

                # =============================================================
                # FILTER FITUR TERLALU KECIL
                # Jika panjang total garis < 2x toleransi efektif,
                # tidak ada bend bermakna yang bisa dideteksi → pertahankan utuh.
                # Ini mencegah pulau kecil yang lolos seleksi jadi garis lurus.
                # =============================================================
                total_panjang = sum(
                    sum(_dist(raw[i][0], raw[i][1], raw[i+1][0], raw[i+1][1])
                        for i in range(len(raw)-1))
                    for raw in [[(p.x(), p.y()) for p in r] for r in raw_lines]
                    if len(raw) >= 2
                )
                if total_panjang < tol_eff_crs * 2.0:
                    is_terlalu_kecil = True
                    cnt_terlalu_kecil += 1

                if not is_terlalu_kecil:
                    success  = False
                    attempts = [1.0, 0.5, 0.25]

                    for multiplier in attempts:
                        current_tol_crs = tol_eff_crs * multiplier
                        current_max_seg = current_tol_crs * MAX_SEG_FACTOR

                        result_lines = []
                        t_orig = t_simp = t_bend = t_c1 = t_c2 = 0

                        for raw in raw_lines:
                            if len(raw) < 2:
                                result_lines.append([(p.x(), p.y()) for p in raw])
                                t_orig += len(raw)
                                t_simp += len(raw)
                                continue

                            orig_xy = [(p.x(), p.y()) for p in raw]
                            t_orig += len(orig_xy)

                            # Fitur < 5 titik: tidak cukup untuk deteksi bend
                            # → pertahankan geometri utuh, jangan disimplifikasi
                            if len(orig_xy) < 5:
                                if multiplier == 1.0:
                                    cnt_pendek += 1
                                result_lines.append(list(orig_xy))
                                t_simp += len(orig_xy)
                                continue

                            simp_xy, n_bend, n_c1, n_c2 = simplify_line(orig_xy, current_tol_crs, current_max_seg)

                            if resolve_topo:
                                simp_xy = _fix_self_intersections(simp_xy, orig_xy)

                            if len(simp_xy) < 2:
                                simp_xy = [orig_xy[0], orig_xy[-1]]

                            t_simp += len(simp_xy)
                            t_bend += n_bend
                            t_c1 += n_c1
                            t_c2 += n_c2
                            result_lines.append(simp_xy)

                        if len(result_lines) == 1:
                            qp = [QgsPoint(x, y) for x, y in result_lines[0]]
                            temp_geom = QgsGeometry(QgsLineString(qp))
                        else:
                            temp_geom = QgsGeometry.fromMultiPolylineXY(
                                [[QgsPointXY(x, y) for x, y in rl] for rl in result_lines]
                            )

                        if temp_geom.isGeosValid() and not temp_geom.isEmpty():
                            out_geom = temp_geom
                            feat_orig, feat_simp, feat_bend, feat_c1, feat_c2 = t_orig, t_simp, t_bend, t_c1, t_c2
                            success = True

                            if multiplier < 1.0:
                                feedback.pushInfo(f"Fitur ID {feat_id} diselamatkan dengan menurunkan toleransi menjadi {multiplier*100}%.")
                                is_retried = True
                            break

                    if not success:
                        feedback.reportError(f"Fitur ID {feat_id} — tetap invalid, dikembalikan ke geometri asli.", fatalError=False)
                        out_geom  = geom
                        feat_orig = sum(len(raw) for raw in raw_lines)
                        feat_simp = feat_orig
                        feat_bend = feat_c1 = feat_c2 = 0
                        is_fallback = True

            s_orig += feat_orig
            s_simp += feat_simp
            s_bend += feat_bend
            s_c1   += feat_c1
            s_c2   += feat_c2

            pct = round((1 - feat_simp / feat_orig) * 100, 1) if feat_orig > 0 else 0.0

            # =================================================================
            # PENENTUAN STATUS LOG (OUTPUT ATTRIBUTE TABLE)
            # =================================================================
            if is_morph_skip:
                stat_simp = 'Diabaikan'
                cnt_skip_morph += 1
            elif is_gri_skip:
                stat_simp = 'Diabaikan'
                cnt_skip_gri += 1
            elif is_terlalu_kecil:
                stat_simp = 'Tetap (Terlalu Kecil)'
                cnt_same += 1
            elif is_fallback:
                stat_simp = 'Tetap (Invalid)'
                cnt_fallback += 1
            elif is_retried:
                stat_simp = 'Tersimplifikasi (Tol. Turun)'
                cnt_retry += 1
                cnt_simp += 1
            elif feat_simp < feat_orig:
                stat_simp = 'Tersimplifikasi'
                cnt_simp += 1
            else:
                stat_simp = 'Tetap'
                cnt_same += 1

            kept_attrs = [feat.attributes()[i] for i in keep_indices]
            new_attrs  = [morph_class, round(tol_eff_m, 3), feat_orig, feat_simp,
                          pct, feat_bend, feat_c1, feat_c2, stat_simp]

            out_feat = QgsFeature(out_fields)
            out_feat.setGeometry(out_geom)
            out_feat.setAttributes(kept_attrs + new_attrs)
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

            processed += 1
            feedback.setProgress(int(processed / total * 100))

        pct_tot = round((1 - s_simp / s_orig) * 100, 1) if s_orig > 0 else 0.0

        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Total fitur diproses     : {processed}')
        feedback.pushInfo(f'  Tersimplifikasi        : {cnt_simp}')
        if cnt_retry > 0:
            feedback.pushInfo(f'    -> Diselamatkan (Toleransi Turun): {cnt_retry}')
        feedback.pushInfo(f'  Tetap (Normal)         : {cnt_same}')
        feedback.pushInfo(f'  Tetap (Invalid/Dikembalikan): {cnt_fallback}')
        feedback.pushInfo(f'  Diabaikan (Orthogonal) : {cnt_skip_morph}')
        feedback.pushInfo(f'  Diabaikan (GRI=1)      : {cnt_skip_gri}')
        if cnt_terlalu_kecil > 0:
            feedback.pushInfo(f'  Tetap (Terlalu Kecil)  : {cnt_terlalu_kecil}  (panjang < 2x tol, dipertahankan utuh)')
        feedback.pushInfo(f'Total titik awal         : {s_orig}')
        feedback.pushInfo(f'Total titik akhir        : {s_simp}')
        feedback.pushInfo(f'Reduksi titik total      : {pct_tot:.1f} %')
        feedback.pushInfo(f'Total bend terdeteksi    : {s_bend} (C1: {s_c1} | C2: {s_c2})')
        if cnt_pendek > 0:
            feedback.pushInfo(f'  INFO: {cnt_pendek} fitur < 5 titik → dipertahankan utuh otomatis.')
        feedback.pushInfo('=' * 60)

        return {self.OUTPUT: dest_id}
