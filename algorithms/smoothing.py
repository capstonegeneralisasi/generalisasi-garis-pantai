# =============================================================================
# SMOOTHING - Generalisasi Garis Pantai
# =============================================================================
# Toolbox : Generalisasi Garis Pantai
# Tahap   : 7 - Smoothing
# Input   : Layer garis pantai hasil Tahap 6 Simplifikasi
#           Skala input dan skala target
#           Kolom morfologi
# Output  : Layer garis pantai yang sudah dihaluskan dengan kolom tambahan:
#             - smooth_applied  : True jika smoothing diterapkan, False jika SKIP
#             - smooth_skip_rsn : alasan SKIP (jika ada)
#             - smooth_morph    : morfologi yang dibaca
#             - smooth_scl_in   : skala input
#             - smooth_scl_out  : skala target
#             - smooth_iter     : jumlah iterasi yang digunakan
#             - smooth_offset   : nilai offset yang digunakan
#
# Parameter smoothing per skala target:
#   1:25.000    → 3 iterasi, offset_base 0.30
#   1:50.000    → 5 iterasi, offset_base 0.35
#   1:250.000   → 8 iterasi, offset_base 0.40
#   1:500.000   → 10 iterasi, offset_base 0.45
#   1:1.000.000 → 15 iterasi, offset_base 0.50
#
# Aturan morfologi:
#   Smooth     → offset × 1.00
#   Broad      → offset × 0.85
#   Elongated  → offset × 0.60 
#   Rugged     → offset × 0.30
#   Orthogonal → SKIP
# =============================================================================

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterEnum,
    QgsProcessingParameterField,
    QgsProcessingException,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)

# =============================================================================
#  KONFIGURASI SKALA
# =============================================================================

SCALE_OPTIONS = {
    '1:5.000':     (5_000,     None, None, None),
    '1:25.000':    (25_000,    20,   3,    0.30),
    '1:50.000':    (50_000,    40,   5,    0.35),
    '1:250.000':   (250_000,   200,  8,    0.40),
    '1:500.000':   (500_000,   400,  10,   0.45),
    '1:1.000.000': (1_000_000, 800,  15,   0.50),
}

INPUT_SCALE_KEYS  = ['1:5.000', '1:25.000', '1:50.000', '1:250.000', '1:500.000']
TARGET_SCALE_KEYS = ['1:25.000', '1:50.000', '1:250.000', '1:500.000', '1:1.000.000']

MIN_DISTANCE_M = 1.0

# =============================================================================
#  KONFIGURASI MORFOLOGI
# =============================================================================

MORPHOLOGY_CONFIG = {
    'Elongated':  (False, 0.60, 130.0, 'Smoothing ringan'),
    'Broad':      (False, 0.85, 180.0, 'Smoothing agresif'),
    'Smooth':     (False, 1.00, 180.0, 'Smoothing penuh'),
    'Rugged':     (False, 0.30, 120.0,   'Smoothing minimal'),
    'Orthogonal': (True,  0.00, 0.0,   'SKIP — sudut 90° buatan tidak boleh dibulatkan'),
}

DEFAULT_MORPH_FIELD = 'pred_class'

OUT_APPLIED     = 'smooth_applied'
OUT_SKIP_REASON = 'smooth_skip_rsn'
OUT_MORPHOLOGY  = 'smooth_morph'
OUT_SCALE_IN    = 'smooth_scl_in'
OUT_SCALE_OUT   = 'smooth_scl_out'
OUT_ITERATIONS  = 'smooth_iter'
OUT_OFFSET      = 'smooth_offset'


def _write_skip(out_feat, reason, morph):
    out_feat.setAttribute(OUT_APPLIED,     False)
    out_feat.setAttribute(OUT_SKIP_REASON, reason[:254])
    out_feat.setAttribute(OUT_MORPHOLOGY,  morph)
    out_feat.setAttribute(OUT_ITERATIONS,  0)
    out_feat.setAttribute(OUT_OFFSET,      0.0)


# =============================================================================
#  ALGORITMA UTAMA
# =============================================================================

class Smoothing(QgsProcessingAlgorithm):

    INPUT        = 'INPUT'
    SKALA_INPUT  = 'SKALA_INPUT'
    SKALA_TARGET = 'SKALA_TARGET'
    MORPH_FIELD  = 'MORPH_FIELD'
    OUTPUT       = 'OUTPUT'

    def name(self):
        return 'smoothing'

    def displayName(self):
        return 'Tahap 7 - Smoothing'

    def group(self):
        return 'Generalisasi Garis Pantai'

    def groupId(self):
        return 'generalisasi_garis_pantai'

    def createInstance(self):
        return Smoothing()

    def tr(self, string):
        return QCoreApplication.translate('Smoothing', string)

    def shortHelpString(self):
        return (
            '<b>Tahap 7 - Smoothing</b><br>'
            '<i>Penghalusan Garis Pantai Berbasis Morfologi — Generalisasi Garis Pantai</i><br><br>'
            'Menghaluskan geometri garis pantai menggunakan algoritma PAEK adaptif '
            'berdasarkan kelas morfologi. Parameter smoothing ditentukan otomatis '
            'sesuai skala target yang dipilih.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: hasil Tahap 6 Simplifikasi<br>'
            '- <i>Skala Input</i>: skala sumber data<br>'
            '- <i>Skala Target</i>: skala hasil generalisasi yang diinginkan<br>'
            '- <i>Kolom Morfologi</i>: kolom berisi kelas morfologi garis pantai<br><br>'
            '<b>Parameter otomatis per skala target:</b><br>'
            '1:25.000 → 3× iter, offset 0.30<br>'
            '1:50.000 → 5× iter, offset 0.35<br>'
            '1:250.000 → 8× iter, offset 0.40<br>'
            '1:500.000 → 10× iter, offset 0.45<br>'
            '1:1.000.000 → 15× iter, offset 0.50<br><br>'
            '<b>Aturan morfologi:</b><br>'
            '- Smooth → offset × 1.00 (paling agresif)<br>'
            '- Broad → offset × 0.85<br>'
            '- Elongated → offset × 0.60 (ringan)<br>'
            '- Rugged → offset × 0.30 (paling ringan)<br>'
            '- Orthogonal → SKIP<br><br>'
            '<b>Output — 7 kolom tambahan:</b><br>'
            '- smooth_applied, smooth_skip_rsn, smooth_morph,<br>'
            '&nbsp;&nbsp;smooth_scl_in, smooth_scl_out, smooth_iter, smooth_offset<br><br>'
            '<b>Catatan:</b><br>'
            'Ini adalah tahap terakhir generalisasi garis pantai. '
            'Output otomatis diproyeksikan ke <b>EPSG:4326 (WGS 84)</b> sebagai hasil akhir. '
            'Lanjutkan ke Topology Editing dan Quality Control.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT,
            'Layer Garis Pantai (hasil Tahap 6 Simplifikasi)',
            [QgsProcessing.TypeVectorLine]
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_INPUT,
            'Skala Input (skala sumber data)',
            options=INPUT_SCALE_KEYS,
            defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_TARGET,
            'Skala Target (skala hasil generalisasi)',
            options=TARGET_SCALE_KEYS,
            defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterField(
            self.MORPH_FIELD,
            'Kolom Morfologi (berisi: Elongated / Broad / Smooth / Rugged / Orthogonal)',
            defaultValue=DEFAULT_MORPH_FIELD,
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Any,
            optional=False
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            'Output - Smoothing'
        ))

    def processAlgorithm(self, parameters, context, feedback):
        source       = self.parameterAsSource(parameters, self.INPUT, context)
        scale_in_idx = self.parameterAsEnum(parameters, self.SKALA_INPUT, context)
        scale_tg_idx = self.parameterAsEnum(parameters, self.SKALA_TARGET, context)
        morph_field  = self.parameterAsString(parameters, self.MORPH_FIELD, context)

        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        scale_in_lbl = INPUT_SCALE_KEYS[scale_in_idx]
        scale_tg_lbl = TARGET_SCALE_KEYS[scale_tg_idx]

        denom_in            = SCALE_OPTIONS[scale_in_lbl][0]
        denom_tg, tol_simp, n_iter, ob = SCALE_OPTIONS[scale_tg_lbl]

        if denom_tg <= denom_in:
            raise QgsProcessingException(
                f'ERROR: Skala target ({scale_tg_lbl}) harus lebih kecil dari '
                f'skala input ({scale_in_lbl}). '
                f'Generalisasi hanya bisa dari skala besar ke skala kecil.'
            )

        if tol_simp is None:
            raise QgsProcessingException(
                f"Skala target '{scale_tg_lbl}' tidak tersedia sebagai target. "
                f"Pilih salah satu dari: {', '.join(TARGET_SCALE_KEYS)}"
            )

        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 7 - SMOOTHING | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Skala input        : {scale_in_lbl}')
        feedback.pushInfo(f'Skala target       : {scale_tg_lbl}')
        feedback.pushInfo(f'Iterasi            : {n_iter}x')
        feedback.pushInfo(f'offset_base        : {ob}')
        feedback.pushInfo(f'minimumDistance    : {MIN_DISTANCE_M} m (fixed)')
        feedback.pushInfo(f'Kolom morfologi    : {morph_field}')
        feedback.pushInfo(f'CRS proses         : EPSG:3857')
        feedback.pushInfo(f'CRS output         : EPSG:4326 (WGS 84)')
        feedback.pushInfo(f'Offset aktual per morfologi:')
        for morph, (skip, of, max_ang, _) in MORPHOLOGY_CONFIG.items():
            if skip:
                feedback.pushInfo(f'  {morph:<12} → SKIP')
            else:
                feedback.pushInfo(f'  {morph:<12} → offset {round(ob*of,3)} ({ob} x {of}), maxAngle {max_ang}°')
        feedback.pushInfo('=' * 60)

        # ---------------------------------------------------------------
        # SIAPKAN REPROYEKSI EPSG:3857 → EPSG:4326 untuk output akhir
        # ---------------------------------------------------------------
        CRS_4326 = QgsCoordinateReferenceSystem('EPSG:4326')
        CRS_3857 = QgsCoordinateReferenceSystem('EPSG:3857')
        src_crs  = source.sourceCrs()

        if src_crs.authid() != 'EPSG:3857':
            feedback.pushInfo(
                f'PERINGATAN: CRS layer input ({src_crs.authid()}) bukan EPSG:3857. '
                f'Pastikan layer input adalah output dari Tahap 6 Simplifikasi '
                f'yang sudah dalam EPSG:3857.'
            )

        transform_to_4326 = QgsCoordinateTransform(
            CRS_3857, CRS_4326, QgsProject.instance()
        )

        field_names = [f.name() for f in source.fields()]
        if morph_field not in field_names:
            raise QgsProcessingException(
                f"Kolom '{morph_field}' tidak ditemukan di layer. "
                f"Kolom yang tersedia: {', '.join(field_names)}"
            )

        out_fields = source.fields()
        out_fields.append(QgsField(OUT_APPLIED,     QVariant.Bool,   len=1))
        out_fields.append(QgsField(OUT_SKIP_REASON, QVariant.String, len=254))
        out_fields.append(QgsField(OUT_MORPHOLOGY,  QVariant.String, len=50))
        out_fields.append(QgsField(OUT_SCALE_IN,    QVariant.String, len=20))
        out_fields.append(QgsField(OUT_SCALE_OUT,   QVariant.String, len=20))
        out_fields.append(QgsField(OUT_ITERATIONS,  QVariant.Int,    len=3))
        out_fields.append(QgsField(OUT_OFFSET,      QVariant.Double, len=6, prec=3))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            out_fields, source.wkbType(), CRS_4326  # output akhir EPSG:4326
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        n_total = n_smoothed = n_skip_morph = n_skip_unknown = n_skip_vertex = 0
        skip_tally = {}
        total = source.featureCount()

        for i, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(i / total * 100))
            n_total += 1

            out_feat = QgsFeature(out_fields)
            out_feat.setGeometry(feat.geometry())
            for field in source.fields():
                out_feat.setAttribute(field.name(), feat[field.name()])
            out_feat.setAttribute(OUT_SCALE_IN,   scale_in_lbl)
            out_feat.setAttribute(OUT_SCALE_OUT,  scale_tg_lbl)
            out_feat.setAttribute(OUT_ITERATIONS, n_iter)

            geom = feat.geometry()

            n_verts = geom.constGet().nCoordinates() if geom else 0
            if n_verts < 4:
                reason = (
                    f'SKIP — vertex terlalu sedikit ({n_verts} titik, '
                    f'minimum 4). Geometri asli dipertahankan.'
                )
                _write_skip(out_feat, reason, '—')
                # Reproyeksi geometri asli ke EPSG:4326 sebelum disimpan
                g = out_feat.geometry()
                g.transform(transform_to_4326)
                out_feat.setGeometry(g)
                sink.addFeature(out_feat)
                n_skip_vertex += 1
                skip_tally['Vertex < 4'] = skip_tally.get('Vertex < 4', 0) + 1
                continue

            raw   = feat[morph_field]
            morph = None
            if raw is not None and str(raw).strip() not in ('', 'NULL'):
                morph = str(raw).strip().capitalize()

            if morph is None or morph not in MORPHOLOGY_CONFIG:
                reason = (
                    f"SKIP — morfologi '{raw}' tidak dikenali atau kosong. "
                    f"Nilai valid: {', '.join(MORPHOLOGY_CONFIG.keys())}"
                )
                _write_skip(out_feat, reason, str(raw) if raw else 'NULL')
                # Reproyeksi geometri asli ke EPSG:4326 sebelum disimpan
                g = out_feat.geometry()
                g.transform(transform_to_4326)
                out_feat.setGeometry(g)
                sink.addFeature(out_feat)
                n_skip_unknown += 1
                key = f'Tidak dikenali/NULL: {raw}'
                skip_tally[key] = skip_tally.get(key, 0) + 1
                continue

            skip, of, max_angle, label = MORPHOLOGY_CONFIG[morph]

            if skip:
                _write_skip(out_feat, label, morph)
                # Reproyeksi geometri asli ke EPSG:4326 sebelum disimpan
                g = out_feat.geometry()
                g.transform(transform_to_4326)
                out_feat.setGeometry(g)
                sink.addFeature(out_feat)
                n_skip_morph += 1
                skip_tally[morph] = skip_tally.get(morph, 0) + 1
                continue

            actual_offset = ob * of
            smoothed = geom.smooth(
                iterations=n_iter,
                offset=actual_offset,
                minimumDistance=MIN_DISTANCE_M,
                maxAngle=max_angle
            )

            if smoothed is None or smoothed.isEmpty():
                feedback.reportError(
                    f'FID {feat.id()} ({morph}): hasil smoothing kosong, geometri asli dipertahankan.'
                )
                smoothed = geom

            # Reproyeksi hasil smoothing ke EPSG:4326 sebelum disimpan
            smoothed.transform(transform_to_4326)

            out_feat.setGeometry(smoothed)
            out_feat.setAttribute(OUT_APPLIED,     True)
            out_feat.setAttribute(OUT_SKIP_REASON, '')
            out_feat.setAttribute(OUT_MORPHOLOGY,  morph)
            out_feat.setAttribute(OUT_OFFSET,      round(actual_offset, 3))
            sink.addFeature(out_feat)
            n_smoothed += 1

        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'CRS proses              : EPSG:3857')
        feedback.pushInfo(f'CRS output              : EPSG:4326 (WGS 84)')
        feedback.pushInfo(f'Total fitur diproses      : {n_total}')
        feedback.pushInfo(f'Berhasil di-smooth        : {n_smoothed}')
        feedback.pushInfo(f'SKIP (Orthogonal)         : {n_skip_morph}')
        feedback.pushInfo(f'SKIP (vertex < 4)         : {n_skip_vertex}')
        feedback.pushInfo(f'SKIP (tidak dikenali)     : {n_skip_unknown}')
        if skip_tally:
            feedback.pushInfo('Detail SKIP:')
            for k, v in skip_tally.items():
                feedback.pushInfo(f'  [{v:4d}]  {k}')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(
            'Tahap generalisasi selesai. Output tersimpan dalam EPSG:4326 (WGS 84). '
            'Lanjutkan ke Topology Editing dan Quality Control.'
        )

        return {self.OUTPUT: dest_id}


def classFactory(iface):
    pass
