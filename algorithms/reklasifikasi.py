# =============================================================================
# REKLASIFIKASI - Generalisasi Garis Pantai
# =============================================================================
# Toolbox : Generalisasi Garis Pantai
# Tahap   : 2 - Reklasifikasi
# Input   : Layer garis pantai hasil Tahap 1 Pengecekan MMU
#           Layer Gazetteer Indonesia (titik)
#           Skala target
#           Radius pencarian (meter)
#           Kolom morfologi (default: pred_class)
# Output  : Layer yang sama dengan kolom GRI tambahan:
#             - GRI : 1 jika segmen memiliki nama dalam Gazetteer Indonesia,
#                     0 jika tidak
#
# Logika:
#   Segmen dengan kelas_bentuk 'Garis' atau 'Poligon' otomatis GRI = 0.
#   Hanya segmen dengan kelas_bentuk 'Titik' yang dicari pasangannya
#   di Gazetteer Indonesia berdasarkan jarak terdekat dalam radius pencarian.
#   Geometri garis pantai tidak dimodifikasi.
#
#   Tambahan:
#   Segmen dengan pulau_kecil = 1 akan di-set kolom morfologinya menjadi NULL.
#   Ini dilakukan agar tahap Simplifikasi dan Smoothing melewati (SKIP)
#   segmen tersebut karena tidak ada kelas morfologi yang terdefinisi.
#
# Referensi:
#   - Gazetteer Resmi Indonesia (GRI) - BIG
# =============================================================================

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterDistance,
    QgsProcessingParameterField,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsSpatialIndex,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsProcessingException,
)


class Reklasifikasi(QgsProcessingAlgorithm):

    INPUT_COASTLINE = 'INPUT_COASTLINE'
    TIPE_FIELD      = 'TIPE_FIELD'
    MORPH_FIELD     = 'MORPH_FIELD'
    INPUT_GAZETTEER = 'INPUT_GAZETTEER'
    SKALA_TARGET    = 'SKALA_TARGET'
    SEARCH_RADIUS   = 'SEARCH_RADIUS'
    OUTPUT          = 'OUTPUT'

    SKALA_TARGET_OPTIONS = [
        '1:25.000',
        '1:50.000',
        '1:250.000',
        '1:500.000',
        '1:1.000.000',
    ]

    TARGET_CRS = QgsCoordinateReferenceSystem('EPSG:3857')

    def name(self):
        return 'reklasifikasi'

    def displayName(self):
        return 'Tahap 2 - Reklasifikasi'

    def group(self):
        return 'Generalisasi Garis Pantai'

    def groupId(self):
        return 'generalisasi_garis_pantai'

    def createInstance(self):
        return Reklasifikasi()

    def tr(self, string):
        return QCoreApplication.translate('Reklasifikasi', string)

    def shortHelpString(self):
        return (
            '<b>Tahap 2 - Reklasifikasi</b><br>'
            '<i>Penentuan Status GRI — Generalisasi Garis Pantai</i><br><br>'
            'Menentukan apakah setiap segmen garis pantai memiliki nama resmi '
            'dalam Gazetteer Resmi Indonesia (GRI). Hanya segmen dengan '
            'kelas_bentuk = Titik yang dicari pasangannya di layer Gazetteer.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: hasil Tahap 1 Pengecekan MMU<br>'
            '- <i>Kolom Kelas Bentuk</i>: kolom berisi nilai Titik / Garis / Poligon<br>'
            '- <i>Kolom Morfologi</i>: kolom berisi kelas morfologi (default: pred_class)<br>'
            '- <i>Layer Gazetteer</i>: titik nama pulau dari Gazetteer Indonesia<br>'
            '- <i>Skala Target</i>: skala hasil generalisasi yang diinginkan<br>'
            '- <i>Radius Pencarian</i>: jarak maksimum pencarian nama GRI (meter)<br><br>'
            '<b>Output — 1 kolom tambahan di attribute table:</b><br>'
            '- <i>GRI</i>: 1 jika segmen memiliki nama GRI, 0 jika tidak<br><br>'
            '<b>Perlakuan khusus pulau kecil:</b><br>'
            'Segmen dengan pulau_kecil = 1 akan di-set kolom morfologinya menjadi NULL. '
            'Hal ini menyebabkan tahap Simplifikasi dan Smoothing melewati (SKIP) '
            'segmen tersebut karena tidak ada kelas morfologi yang terdefinisi.<br><br>'
            '<b>Catatan:</b><br>'
            'Segmen dengan kelas_bentuk Garis atau Poligon otomatis GRI = 0. '
            'Geometri tidak dimodifikasi. '
            'Lanjutkan ke Tahap 3 - Seleksi dan Eliminasi.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT_COASTLINE,
            'Layer Garis Pantai (hasil Tahap 1 Pengecekan MMU)',
            [QgsProcessing.TypeVectorLine]
        ))
        self.addParameter(QgsProcessingParameterField(
            self.TIPE_FIELD,
            'Kolom Kelas Bentuk (berisi: Titik / Garis / Poligon)',
            defaultValue='kelas_bentuk',
            parentLayerParameterName=self.INPUT_COASTLINE,
            type=QgsProcessingParameterField.Any
        ))
        self.addParameter(QgsProcessingParameterField(
            self.MORPH_FIELD,
            'Kolom Morfologi (akan di-NULL untuk pulau kecil)',
            defaultValue='pred_class',
            parentLayerParameterName=self.INPUT_COASTLINE,
            type=QgsProcessingParameterField.Any
        ))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT_GAZETTEER,
            'Layer Gazetteer Indonesia (titik nama pulau)',
            [QgsProcessing.TypeVectorPoint]
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_TARGET,
            'Skala Target (skala hasil generalisasi)',
            options=self.SKALA_TARGET_OPTIONS,
            defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterDistance(
            self.SEARCH_RADIUS,
            'Radius Pencarian GRI (meter)',
            defaultValue=100.0,
            minValue=1.0,
            parentParameterName=self.INPUT_COASTLINE
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            'Output - Reklasifikasi'
        ))

    def processAlgorithm(self, parameters, context, feedback):
        coastline_source = self.parameterAsSource(parameters, self.INPUT_COASTLINE, context)
        tipe_field       = self.parameterAsString(parameters, self.TIPE_FIELD, context)
        morph_field      = self.parameterAsString(parameters, self.MORPH_FIELD, context)
        gazetteer_source = self.parameterAsSource(parameters, self.INPUT_GAZETTEER, context)
        idx_st           = self.parameterAsEnum(parameters, self.SKALA_TARGET, context)
        search_radius    = self.parameterAsDouble(parameters, self.SEARCH_RADIUS, context)
        label_target     = self.SKALA_TARGET_OPTIONS[idx_st]

        if coastline_source is None or gazetteer_source is None:
            raise QgsProcessingException('Layer input tidak valid atau tidak terbaca.')

        # Validasi kolom morfologi
        field_names = [f.name() for f in coastline_source.fields()]
        if morph_field not in field_names:
            raise QgsProcessingException(
                f"Kolom morfologi '{morph_field}' tidak ditemukan di layer. "
                f"Kolom yang tersedia: {', '.join(field_names)}"
            )

        # Validasi kolom pulau_kecil
        has_pulau_kecil = 'pulau_kecil' in field_names
        if not has_pulau_kecil:
            feedback.reportError(
                "Kolom 'pulau_kecil' tidak ditemukan di layer. "
                "Fitur NULL morfologi untuk pulau kecil tidak akan diterapkan. "
                "Pastikan input adalah output dari Tahap 1 Pengecekan MMU.",
                fatalError=False
            )

        coast_transform = None
        if coastline_source.sourceCrs() != self.TARGET_CRS:
            coast_transform = QgsCoordinateTransform(
                coastline_source.sourceCrs(), self.TARGET_CRS, QgsProject.instance()
            )

        gaz_transform = None
        if gazetteer_source.sourceCrs() != self.TARGET_CRS:
            gaz_transform = QgsCoordinateTransform(
                gazetteer_source.sourceCrs(), self.TARGET_CRS, QgsProject.instance()
            )

        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 2 - REKLASIFIKASI | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Skala target      : {label_target}')
        feedback.pushInfo(f'Kolom kelas bentuk: {tipe_field}')
        feedback.pushInfo(f'Kolom morfologi   : {morph_field}')
        feedback.pushInfo(f'Radius pencarian  : {search_radius} m')
        feedback.pushInfo(f'Kolom pulau_kecil : {"ditemukan" if has_pulau_kecil else "tidak ditemukan — fitur NULL morfologi dinonaktifkan"}')
        feedback.pushInfo('=' * 60)

        # =====================================================================
        # TAHAP 1: Baca layer garis pantai, filter segmen Titik untuk GRI
        # =====================================================================
        feedback.pushInfo('Membaca layer garis pantai dan memfilter segmen Titik...')
        coastlines_dict = {}
        gri_values      = {}
        coastline_index = QgsSpatialIndex()
        n_titik = n_bukan_titik = 0

        for feat in coastline_source.getFeatures():
            if feedback.isCanceled():
                return {}
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            if coast_transform:
                geom.transform(coast_transform)

            transformed_feat = QgsFeature(feat)
            transformed_feat.setGeometry(geom)
            coastlines_dict[feat.id()] = transformed_feat
            gri_values[feat.id()] = 0

            nilai_tipe = str(feat[tipe_field]).strip().lower() \
                if tipe_field in feat.fields().names() else ''

            if 'titik' in nilai_tipe or 'point' in nilai_tipe:
                coastline_index.addFeature(transformed_feat)
                n_titik += 1
            else:
                n_bukan_titik += 1

        feedback.pushInfo(f'Segmen Titik (dicari GRI)      : {n_titik}')
        feedback.pushInfo(f'Segmen Garis/Poligon (GRI = 0) : {n_bukan_titik}')

        # =====================================================================
        # TAHAP 2: Cocokkan titik Gazetteer ke segmen Titik terdekat
        # =====================================================================
        feedback.pushInfo('Mencocokkan titik Gazetteer ke segmen Titik terdekat...')
        total_points = gazetteer_source.featureCount()
        step    = 100.0 / total_points if total_points else 1
        n_cocok = 0

        for current, point_feat in enumerate(gazetteer_source.getFeatures()):
            if feedback.isCanceled():
                break

            pt_geom = point_feat.geometry()
            if pt_geom is None or pt_geom.isEmpty():
                continue
            if gaz_transform:
                pt_geom.transform(gaz_transform)

            search_rect = pt_geom.boundingBox()
            search_rect.grow(search_radius)
            candidate_ids = coastline_index.intersects(search_rect)

            nearest_id = None
            min_dist   = float('inf')

            for cid in candidate_ids:
                dist = pt_geom.distance(coastlines_dict[cid].geometry())
                if dist < min_dist:
                    min_dist   = dist
                    nearest_id = cid

            if nearest_id is not None and min_dist <= search_radius:
                gri_values[nearest_id] = 1
                n_cocok += 1

            feedback.setProgress(int(current * step))

        feedback.pushInfo(f'Titik Gazetteer dicocokkan      : {n_cocok}')

        # =====================================================================
        # TAHAP 3: Tulis output — tambah kolom GRI, NULL morfologi pulau kecil
        # =====================================================================
        feedback.pushInfo('Menyimpan output...')

        output_fields = QgsFields(coastline_source.fields())
        if 'GRI' not in [f.name().upper() for f in output_fields]:
            output_fields.append(QgsField('GRI', QVariant.Int))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            output_fields, coastline_source.wkbType(), self.TARGET_CRS
        )
        if sink is None:
            raise QgsProcessingException('Gagal membuat layer output.')

        n_null_morph = 0

        for fid, feat in coastlines_dict.items():
            out_feat = QgsFeature(output_fields)
            out_feat.setGeometry(feat.geometry())

            # Salin semua atribut lama
            for field in coastline_source.fields():
                out_feat.setAttribute(field.name(), feat[field.name()])

            # Set GRI
            out_feat.setAttribute('GRI', gri_values[fid])

            # NULL morfologi untuk pulau kecil
            if has_pulau_kecil:
                try:
                    pulau_kecil_val = int(feat['pulau_kecil'])
                except (TypeError, ValueError):
                    pulau_kecil_val = 0

                if pulau_kecil_val == 1:
                    out_feat.setAttribute(morph_field, None)
                    n_null_morph += 1

            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

        n_gri1 = sum(1 for v in gri_values.values() if v == 1)
        n_gri0 = sum(1 for v in gri_values.values() if v == 0)

        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Total fitur input       : {len(coastlines_dict)}')
        feedback.pushInfo(f'GRI = 1 (ada nama)      : {n_gri1} fitur')
        feedback.pushInfo(f'GRI = 0 (tidak ada nama): {n_gri0} fitur')
        feedback.pushInfo(f'Morfologi di-NULL       : {n_null_morph} fitur (pulau_kecil = 1)')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(
            'Lanjutkan ke Tahap 3 - Seleksi dan Eliminasi.'
        )

        return {self.OUTPUT: dest_id}
