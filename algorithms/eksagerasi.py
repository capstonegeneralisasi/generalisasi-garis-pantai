# =============================================================================
# EKSAGERASI - Generalisasi Garis Pantai  (v1.3 - revisi terbaru)
# =============================================================================
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingException,
    QgsWkbTypes, QgsFeature, QgsGeometry, QgsField, QgsFields,
    QgsSpatialIndex, QgsPointXY, QgsFeatureSink,
)

ALL_SCALES = [
    ('1:5.000',       5_000),
    ('1:25.000',      25_000),
    ('1:50.000',      50_000),
    ('1:250.000',     250_000),
    ('1:500.000',     500_000),
    ('1:1.000.000', 1_000_000),
]
INPUT_SCALE_OPTIONS  = ALL_SCALES
OUTPUT_SCALE_OPTIONS = ALL_SCALES[1:]
MIN_SYMBOL_MM = 0.5
CONFLICT_MM   = 0.15
KELAS_TITIK   = 'Titik'
GRI_LULUS     = 1

def hitung_min_size(d):     return (MIN_SYMBOL_MM / 1000.0) * d
def hitung_conflict_dist(d): return (CONFLICT_MM / 1000.0) * d

def scale_geom_from_centroid(geom, centroid_pt, scale_factor):
    cx, cy = centroid_pt.x(), centroid_pt.y()
    def _scale_pts(pts):
        return [QgsPointXY(cx + (p.x()-cx)*scale_factor, cy + (p.y()-cy)*scale_factor) for p in pts]
    flat = QgsWkbTypes.flatType(geom.wkbType())
    if flat == QgsWkbTypes.Polygon:
        return QgsGeometry.fromPolygonXY([_scale_pts(r) for r in geom.asPolygon()])
    elif flat == QgsWkbTypes.MultiPolygon:
        return QgsGeometry.fromMultiPolygonXY([[_scale_pts(r) for r in poly] for poly in geom.asMultiPolygon()])
    elif flat == QgsWkbTypes.LineString:
        return QgsGeometry.fromPolylineXY(_scale_pts(geom.asPolyline()))
    elif flat == QgsWkbTypes.MultiLineString:
        return QgsGeometry.fromMultiPolylineXY([_scale_pts(line) for line in geom.asMultiPolyline()])
    return QgsGeometry(geom)

def deteksi_konflik(geom_baru, spatial_index, geom_tersimpan, conflict_dist, exclude_fid=None):
    bbox = geom_baru.boundingBox(); bbox.grow(conflict_dist)
    for fid in spatial_index.intersects(bbox):
        if fid == exclude_fid: continue
        g = geom_tersimpan.get(fid)
        if g and geom_baru.distance(g) < conflict_dist: return True
    return False

def ambil_centroid(geom):
    if geom is None or geom.isEmpty(): return None
    flat = QgsWkbTypes.flatType(geom.wkbType())
    if flat in (QgsWkbTypes.Point, QgsWkbTypes.MultiPoint):
        pt = geom.asPoint() if flat == QgsWkbTypes.Point else geom.asMultiPoint()[0]
        return QgsPointXY(pt.x(), pt.y())
    if flat in (QgsWkbTypes.Polygon, QgsWkbTypes.MultiPolygon):
        c = geom.centroid()
        if c is None or c.isEmpty(): c = geom.pointOnSurface()
        if c is None or c.isEmpty(): return None
        pt = c.asPoint(); return QgsPointXY(pt.x(), pt.y())
    if flat in (QgsWkbTypes.LineString, QgsWkbTypes.MultiLineString):
        interp = geom.interpolate(geom.length() / 2.0)
        if interp is None or interp.isEmpty(): return None
        pt = interp.asPoint(); return QgsPointXY(pt.x(), pt.y())
    c = geom.centroid()
    if c is None or c.isEmpty(): return None
    pt = c.asPoint(); return QgsPointXY(pt.x(), pt.y())

def eksagerasi_bertahap(geom_asli, centroid_pt, min_size_m,
                        ref_index, ref_geoms, dyn_index, dyn_geoms,
                        conflict_dist, exclude_fid=None):
    bbox    = geom_asli.boundingBox()
    max_dim = max(bbox.width(), bbox.height())
    if max_dim <= 0:
        return QgsGeometry(geom_asli), 1.0, 'Dilewati - Dimensi Nol'
    scale_target = min_size_m / max_dim
    if scale_target <= 1.0:
        return QgsGeometry(geom_asli), 1.0, 'Dipertahankan - Terlihat di Skala Target'
    geom_centroid_pt = QgsGeometry.fromPointXY(centroid_pt)
    def _cek_nabrak(g):
        return (deteksi_konflik(g, ref_index, ref_geoms, conflict_dist, exclude_fid) or
                deteksi_konflik(g, dyn_index, dyn_geoms, conflict_dist))
    geom_asli_q = QgsGeometry(geom_asli)
    if _cek_nabrak(geom_asli_q):
        return geom_asli_q, 1.0, 'Dilewati - Konflik di Ukuran Asli'
    if geom_asli_q.distance(geom_centroid_pt) > min_size_m:
        return geom_asli_q, 1.0, 'Dilewati - GRI di Luar Geometri'
    lo, hi = 1.0, scale_target
    best_geom, best_scale = QgsGeometry(geom_asli), 1.0
    for _ in range(12):
        mid = (lo + hi) / 2.0
        geom_mid = scale_geom_from_centroid(geom_asli, centroid_pt, mid)
        if _cek_nabrak(geom_mid) or geom_mid.distance(geom_centroid_pt) > min_size_m:
            hi = mid
        else:
            best_geom, best_scale, lo = geom_mid, mid, mid
    if abs(best_scale - 1.0) < 0.01:       status = 'Dipertahankan - Tidak Ada Ruang'
    elif abs(best_scale - scale_target) < 0.05: status = 'Eksagerasi Penuh'
    else:                                   status = 'Eksagerasi Sebagian'
    return best_geom, best_scale, status

class Eksagerasi(QgsProcessingAlgorithm):
    INPUT = 'INPUT'; SKALA_INPUT = 'SKALA_INPUT'; SKALA_TARGET = 'SKALA_TARGET'; OUTPUT = 'OUTPUT'

    def name(self):        return 'eksagerasi'
    def displayName(self): return 'Tahap 5 - Eksagerasi'
    def group(self):       return 'Generalisasi Garis Pantai'
    def groupId(self):     return 'generalisasi_garis_pantai'
    def createInstance(self): return Eksagerasi()
    def tr(self, s):       return QCoreApplication.translate('Eksagerasi', s)

    def shortHelpString(self):
        return (
            '<b>Tahap 5 - Eksagerasi</b><br>'
            '<i>Pembesaran Pulau Kecil Berbasis Visibilitas — Generalisasi Garis Pantai</i><br><br>'
            'Membesarkan pulau-pulau kecil yang tidak terlihat di skala target '
            'hingga memenuhi ukuran minimum simbol kartografi (0,5 mm x denominator). '
            'Pembesaran dilakukan secara binary search dari centroid, berhenti jika '
            'menyentuh fitur lain. Seluruh fitur diteruskan ke output.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: hasil Tahap 4 Agregasi (wajib CRS meter)<br>'
            '- <i>Skala Input</i>: skala sumber data<br>'
            '- <i>Skala Target</i>: skala hasil generalisasi yang diinginkan<br><br>'
            '<b>Kriteria eksagerasi:</b><br>'
            '- kelas_bentuk = Titik AND GRI = 1<br>'
            '- Ukuran max_dim &lt; 0,5 mm x denominator skala target<br><br>'
            '<b>Output — 5 kolom tambahan:</b><br>'
            '- skala_input, skala_target, scale_factor, conflict_flag, exag_status<br><br>'
            '<b>Catatan:</b><br>'
            'Indeks referensi mencakup semua fitur non-Titik dan Titik GRI=1. '
            'Fitur conflict_flag=1 dapat dilanjutkan ke alat Displacement. '
            'Lanjutkan ke Tahap 6 - Simplifikasi.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, 'Layer Garis Pantai (hasil Tahap 4 Agregasi, wajib CRS meter)',
            [QgsProcessing.TypeVectorAnyGeometry]))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_INPUT, 'Skala Input (skala sumber data)',
            options=[s[0] for s in INPUT_SCALE_OPTIONS], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_TARGET, 'Skala Target (skala hasil generalisasi)',
            options=[s[0] for s in OUTPUT_SCALE_OPTIONS], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, 'Output - Eksagerasi', QgsProcessing.TypeVectorLine))

    def processAlgorithm(self, parameters, context, feedback):
        source        = self.parameterAsSource(parameters, self.INPUT, context)
        in_scale_idx  = self.parameterAsEnum(parameters, self.SKALA_INPUT, context)
        out_scale_idx = self.parameterAsEnum(parameters, self.SKALA_TARGET, context)
        in_label  = INPUT_SCALE_OPTIONS[in_scale_idx][0]
        in_denom  = INPUT_SCALE_OPTIONS[in_scale_idx][1]
        out_label = OUTPUT_SCALE_OPTIONS[out_scale_idx][0]
        out_denom = OUTPUT_SCALE_OPTIONS[out_scale_idx][1]
        if source is None:
            raise QgsProcessingException('Layer input tidak valid atau tidak terbaca.')
        if in_denom >= out_denom:
            raise QgsProcessingException(
                f'ERROR: Skala target ({out_label}) harus lebih kecil dari skala input ({in_label}).')
        min_size_m    = hitung_min_size(out_denom)
        conflict_dist = hitung_conflict_dist(out_denom)
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 5 - EKSAGERASI | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Skala input        : {in_label}')
        feedback.pushInfo(f'Skala target       : {out_label}')
        feedback.pushInfo(f'Ukuran min simbol  : {min_size_m:.2f} m ({MIN_SYMBOL_MM} mm x {out_denom})')
        feedback.pushInfo(f'Jarak konflik      : {conflict_dist:.2f} m ({CONFLICT_MM} mm x {out_denom})')
        feedback.pushInfo('Target eksagerasi  : kelas_bentuk = Titik AND GRI = 1')
        feedback.pushInfo('=' * 60)
        field_lower = [f.name().lower() for f in source.fields()]
        if 'kelas_bentuk' not in field_lower or 'gri' not in field_lower:
            raise QgsProcessingException(
                "Kolom 'kelas_bentuk' atau 'GRI' tidak ditemukan. Pastikan input dari Tahap 4 Agregasi.")
        field_names_orig = [f.name() for f in source.fields()]
        idx_kelas = field_names_orig.index(next(f for f in field_names_orig if f.lower() == 'kelas_bentuk'))
        idx_gri   = field_names_orig.index(next(f for f in field_names_orig if f.lower() == 'gri'))
        fields = QgsFields()
        for field in source.fields(): fields.append(field)
        fields.append(QgsField('skala_input',   QVariant.String))
        fields.append(QgsField('skala_target',  QVariant.String))
        fields.append(QgsField('scale_factor',  QVariant.Double))
        fields.append(QgsField('conflict_flag', QVariant.Int))
        fields.append(QgsField('exag_status',   QVariant.String))
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.MultiLineString, source.sourceCrs())
        if sink is None:
            raise QgsProcessingException('Gagal membuat layer output.')
        feedback.pushInfo('Membangun indeks spasial referensi (non-Titik + Titik GRI=1)...')
        ref_index = QgsSpatialIndex(); ref_geoms = {}
        for feat_ref in source.getFeatures():
            kelas_ref = str(feat_ref.attributes()[idx_kelas]).strip().lower()
            geom_ref  = feat_ref.geometry()
            if geom_ref is None or geom_ref.isEmpty(): continue
            try:    gri_ref = int(float(feat_ref.attributes()[idx_gri]))
            except: gri_ref = 0
            if kelas_ref != KELAS_TITIK.lower() or gri_ref == GRI_LULUS:
                tmp = QgsFeature(); tmp.setId(feat_ref.id()); tmp.setGeometry(geom_ref)
                ref_index.insertFeature(tmp); ref_geoms[feat_ref.id()] = geom_ref
        feedback.pushInfo(f'{len(ref_geoms)} fitur diindeks sebagai referensi konflik.')
        dyn_index = QgsSpatialIndex(); dyn_geoms = {}
        out_fid = n_eksag = n_konflik = n_debug_target = 0
        total = source.featureCount()
        feedback.pushInfo('Memproses eksagerasi per fitur...')
        for current, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled(): break
            feedback.setProgress(int(current / max(total, 1) * 100))
            kelas_attr = str(feat.attributes()[idx_kelas]).strip().lower()
            try:    gri_val = int(float(feat.attributes()[idx_gri]))
            except: gri_val = 0
            geom_final = feat.geometry(); status_exag = 'Tidak Dieksagerasi'
            final_scale = 1.0; conflict_flag = 0
            if kelas_attr == KELAS_TITIK.lower() and gri_val == GRI_LULUS:
                geom_asli = feat.geometry(); centroid_pt = ambil_centroid(geom_asli)
                if centroid_pt is not None:
                    flat_type = QgsWkbTypes.flatType(geom_asli.wkbType())
                    if flat_type in (QgsWkbTypes.Point, QgsWkbTypes.MultiPoint):
                        geom_asli = QgsGeometry.fromPointXY(centroid_pt).buffer(1.0, 8)
                    if n_debug_target < 20:
                        bbox_dbg  = geom_asli.boundingBox()
                        max_d_dbg = max(bbox_dbg.width(), bbox_dbg.height())
                        st_dbg    = min_size_m / max_d_dbg if max_d_dbg > 0 else 0
                        feedback.pushInfo(
                            f'  [DEBUG] fid={feat.id()} '
                            f'type={QgsWkbTypes.displayString(geom_asli.wkbType())} '
                            f'max_dim={max_d_dbg:.2f}m min_size={min_size_m:.2f}m '
                            f'scale_target={st_dbg:.3f} ("<=1.0"=sudah cukup besar)')
                        n_debug_target += 1
                    geom_final_cand, final_scale, status_exag = eksagerasi_bertahap(
                        geom_asli, centroid_pt, min_size_m,
                        ref_index, ref_geoms, dyn_index, dyn_geoms,
                        conflict_dist, exclude_fid=feat.id())
                    if 'Konflik' in status_exag or 'Tidak Ada Ruang' in status_exag:
                        conflict_flag = 1; n_konflik += 1
                    else:
                        conflict_flag = 0; geom_final = geom_final_cand
                        tmp_dyn = QgsFeature(); tmp_dyn.setId(out_fid); tmp_dyn.setGeometry(geom_final)
                        dyn_index.insertFeature(tmp_dyn); dyn_geoms[out_fid] = geom_final
                        n_eksag += 1
            out_feat = QgsFeature(fields)
            for i, field in enumerate(source.fields()):
                out_feat.setAttribute(field.name(), feat.attribute(i))
            out_feat.setAttribute('skala_input',   in_label)
            out_feat.setAttribute('skala_target',  out_label)
            out_feat.setAttribute('scale_factor',  round(final_scale, 4))
            out_feat.setAttribute('conflict_flag', conflict_flag)
            out_feat.setAttribute('exag_status',   status_exag)
            out_feat.setGeometry(geom_final)
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)
            out_fid += 1
        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Total fitur diproses      : {out_fid}')
        feedback.pushInfo(f'Berhasil dieksagerasi     : {n_eksag}')
        feedback.pushInfo(f'Konflik spasial (flag = 1): {n_konflik}')
        feedback.pushInfo(f'Tidak dieksagerasi        : {out_fid - n_eksag - n_konflik}')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('Lanjutkan ke Tahap 6 - Simplifikasi.')
        return {self.OUTPUT: dest_id}
