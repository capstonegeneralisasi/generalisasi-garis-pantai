# =============================================================================
# AGREGASI - Generalisasi Garis Pantai
# =============================================================================
# Toolbox : Generalisasi Garis Pantai
# Tahap   : 4 - Agregasi
# Input   : Layer garis pantai hasil Tahap 3 Seleksi dan Eliminasi
#           Skala input dan skala target
# Output  : Layer hasil agregasi dengan kolom tambahan:
#             - status_agr : 'Diagregasi' jika pulau digabungkan, 'Tidak' jika tidak
#
# Logika:
#   Hanya segmen dengan kelas_bentuk = 'Titik' AND GRI = 1 yang diagregasi.
#   Segmen lain (Garis, Poligon, atau Titik GRI=0) dilewati tanpa perubahan.
#   Pulau-pulau kecil yang berdekatan digabungkan menggunakan teknik
#   buffer-dissolve-boundary untuk membentuk satu entitas.
#   Anti-self-aggregation: teluk dan pelabuhan tidak ikut tergabung.
#   Anti-intersection: jembatan yang menabrak pulau bypass (non-target) ditolak.
# =============================================================================

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingMultiStepFeedback,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureSink,
    QgsWkbTypes,
    QgsVectorLayer,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsSpatialIndex,
)
from PyQt5.QtCore import QVariant
import processing


class Agregasi(QgsProcessingAlgorithm):

    INPUT        = 'INPUT'
    SKALA_INPUT  = 'SKALA_INPUT'
    SKALA_TARGET = 'SKALA_TARGET'
    OUTPUT       = 'OUTPUT'

    SKALA_INPUT_OPTIONS = [
        '1:5.000',
        '1:25.000',
        '1:50.000',
        '1:250.000',
        '1:500.000',
    ]

    SKALA_TARGET_OPTIONS = [
        '1:25.000',
        '1:50.000',
        '1:250.000',
        '1:500.000',
        '1:1.000.000',
    ]

    SKALA_VALUE = {
        '1:5.000':     5000,
        '1:25.000':    25000,
        '1:50.000':    50000,
        '1:250.000':   250000,
        '1:500.000':   500000,
        '1:1.000.000': 1000000,
    }

    def name(self):
        return 'agregasi'

    def displayName(self):
        return 'Tahap 4 - Agregasi'

    def group(self):
        return 'Generalisasi Garis Pantai'

    def groupId(self):
        return 'generalisasi_garis_pantai'

    def createInstance(self):
        return Agregasi()

    def shortHelpString(self):
        return (
            '<b>Tahap 4 - Agregasi</b><br>'
            '<i>Penggabungan Pulau Kecil — Generalisasi Garis Pantai</i><br><br>'
            'Menggabungkan pulau-pulau kecil yang berdekatan menjadi satu entitas '
            'menggunakan teknik buffer-dissolve-boundary. Hanya segmen dengan '
            'kelas_bentuk = Titik dan GRI = 1 yang diagregasi. Segmen lain '
            'dilewati tanpa perubahan.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: hasil Tahap 3 Seleksi dan Eliminasi<br>'
            '- <i>Skala Input</i>: skala sumber data<br>'
            '- <i>Skala Target</i>: skala hasil generalisasi yang diinginkan<br><br>'
            '<b>Output — 1 kolom tambahan:</b><br>'
            '- <i>status_agr</i>: Diagregasi / Tidak<br><br>'
            '<b>Catatan:</b><br>'
            'Anti-self-aggregation aktif: teluk dan pelabuhan tidak ikut tergabung.<br>'
            'Anti-intersection aktif: jembatan yang menabrak pulau non-target (bypass) ditolak.<br>'
            'CRS output mengikuti CRS layer input. '
            'Lanjutkan ke Tahap 5 - Eksagerasi.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                'Layer Garis Pantai (hasil Tahap 3 Seleksi dan Eliminasi)',
                types=[QgsProcessing.TypeVectorPolygon, QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_INPUT,
            'Skala Input (skala sumber data)',
            options=self.SKALA_INPUT_OPTIONS,
            defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.SKALA_TARGET,
            'Skala Target (skala hasil generalisasi)',
            options=self.SKALA_TARGET_OPTIONS,
            defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            'Output - Agregasi'
        ))

    def processAlgorithm(self, parameters, context, model_feedback):
        idx_si  = self.parameterAsEnum(parameters, self.SKALA_INPUT, context)
        idx_st  = self.parameterAsEnum(parameters, self.SKALA_TARGET, context)

        label_input  = self.SKALA_INPUT_OPTIONS[idx_si]
        label_target = self.SKALA_TARGET_OPTIONS[idx_st]
        s_in  = self.SKALA_VALUE[label_input]
        s_out = self.SKALA_VALUE[label_target]

        if s_out <= s_in:
            raise QgsProcessingException(
                f'ERROR: Skala target ({label_target}) harus lebih kecil dari '
                f'skala input ({label_input}). '
                f'Generalisasi hanya bisa dari skala besar ke skala kecil.'
            )

        buff_dist    = 0.0005 * (s_out - s_in)
        source_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        input_wkb_type = source_layer.wkbType()
        is_line = (QgsWkbTypes.geometryType(input_wkb_type) == QgsWkbTypes.LineGeometry)
        feedback = QgsProcessingMultiStepFeedback(9, model_feedback)

        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 4 - AGREGASI | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Skala input    : {label_input}')
        feedback.pushInfo(f'Skala target   : {label_target}')
        feedback.pushInfo(f'Buffer jarak   : {buff_dist:.2f} m')
        feedback.pushInfo('Target agregasi: kelas_bentuk = Titik AND GRI = 1')
        feedback.pushInfo('=' * 60)

        # ------------------------------------------------------------------
        # 1. Reproyeksi ke EPSG:3857
        # ------------------------------------------------------------------
        feedback.pushInfo('1. Reproyeksi ke EPSG:3857...')
        clean = processing.run(
            'native:dropmzvalues',
            {'INPUT': source_layer, 'OUTPUT': 'memory:'},
            context=context, feedback=feedback, is_child_algorithm=True
        )['OUTPUT']
        proj = processing.run(
            'native:reprojectlayer',
            {'INPUT': clean, 'TARGET_CRS': QgsCoordinateReferenceSystem('EPSG:3857'), 'OUTPUT': 'memory:'},
            context=context, feedback=feedback, is_child_algorithm=True
        )['OUTPUT']
        proj_layer = QgsProcessingUtils.mapLayerFromString(proj, context)

        proj_features = {f.id(): f for f in proj_layer.getFeatures()}
        proj_idx      = QgsSpatialIndex(proj_layer.getFeatures())

        # ------------------------------------------------------------------
        # 2. Filter target agregasi dan bypass
        # ------------------------------------------------------------------
        feedback.pushInfo('2. Memfilter target agregasi (kelas_bentuk = Titik AND GRI = 1)...')
        f_target = []
        f_bypass = []
        for feat in proj_layer.getFeatures():
            kelas = str(feat['kelas_bentuk']).strip().lower() if 'kelas_bentuk' in feat.fields().names() else ''
            try:
                gri = int(feat['GRI'])
            except (TypeError, ValueError):
                gri = 0
            if 'titik' in kelas and gri == 1:
                f_target.append(feat)
            else:
                f_bypass.append(feat)

        feedback.pushInfo(f'   Target agregasi (Titik GRI=1) : {len(f_target)} fitur')
        feedback.pushInfo(f'   Bypass (tidak diagregasi)      : {len(f_bypass)} fitur')

        wkb_str      = 'LineString' if is_line else 'Polygon'
        layer_target = QgsVectorLayer(f'{wkb_str}?crs=EPSG:3857', 'Target_Agr', 'memory')
        layer_target.dataProvider().addAttributes(proj_layer.fields().toList())
        layer_target.updateFields()
        layer_target.dataProvider().addFeatures(f_target)

        layer_bypass = QgsVectorLayer(f'{wkb_str}?crs=EPSG:3857', 'Bypass_Agr', 'memory')
        layer_bypass.dataProvider().addAttributes(proj_layer.fields().toList())
        layer_bypass.updateFields()
        layer_bypass.dataProvider().addFeatures(f_bypass)

        # ------------------------------------------------------------------
        # 3. Bangun spatial index untuk bypass (pulau non-target)
        #    → digunakan nanti untuk cek anti-intersection
        # ------------------------------------------------------------------
        feedback.pushInfo('3. Membangun spatial index pulau bypass untuk anti-intersection...')

        # Konversi bypass ke polygon agar bisa dicek intersection secara spasial
        if is_line:
            bypass_poly_id = processing.run(
                'native:polygonize',
                {'INPUT': layer_bypass, 'KEEP_FIELDS': False, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            bypass_poly_layer = QgsProcessingUtils.mapLayerFromString(bypass_poly_id, context)
        else:
            bypass_poly_layer = layer_bypass

        bypass_single_id = processing.run(
            'native:multiparttosingleparts',
            {'INPUT': bypass_poly_layer, 'OUTPUT': 'memory:'},
            context=context, feedback=feedback, is_child_algorithm=True
        )['OUTPUT']
        bypass_single_layer = QgsProcessingUtils.mapLayerFromString(bypass_single_id, context)

        bypass_idx  = QgsSpatialIndex(bypass_single_layer.getFeatures())
        bypass_dict = {f.id(): f for f in bypass_single_layer.getFeatures()}

        # ------------------------------------------------------------------
        # 4. Merakit topologi target dan membuat UID pulau
        # ------------------------------------------------------------------
        feedback.pushInfo('4. Merakit topologi target dan membuat UID pulau...')
        if is_line:
            polys_raw_id  = processing.run(
                'native:polygonize',
                {'INPUT': layer_target, 'KEEP_FIELDS': False, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            polys_raw = QgsProcessingUtils.mapLayerFromString(polys_raw_id, context)
        else:
            polys_raw = layer_target

        single_polys_id    = processing.run(
            'native:multiparttosingleparts',
            {'INPUT': polys_raw, 'OUTPUT': 'memory:'},
            context=context, feedback=feedback, is_child_algorithm=True
        )['OUTPUT']
        single_polys_layer = QgsProcessingUtils.mapLayerFromString(single_polys_id, context)

        uid_fields = QgsFields()
        uid_fields.append(QgsField('uid', QVariant.String))

        poly_uid_layer = QgsVectorLayer('Polygon?crs=EPSG:3857', 'Poly_UID', 'memory')
        poly_uid_layer.dataProvider().addAttributes(uid_fields.toList())
        poly_uid_layer.updateFields()

        poly_feats  = []
        uid_counter = 0
        for f in single_polys_layer.getFeatures():
            nf = QgsFeature(uid_fields)
            nf.setGeometry(f.geometry())
            nf['uid'] = f'P_{uid_counter}'
            poly_feats.append(nf)
            uid_counter += 1
        poly_uid_layer.dataProvider().addFeatures(poly_feats)

        has_islands      = (uid_counter > 0)
        final_merged_pre = proj_layer

        if has_islands:
            # ------------------------------------------------------------------
            # 5. Membuat jembatan penghubung pulau kecil
            # ------------------------------------------------------------------
            feedback.pushInfo('5. Membuat jembatan penghubung pulau kecil...')
            buf_pos    = processing.run(
                'native:buffer',
                {'INPUT': poly_uid_layer, 'DISTANCE': buff_dist, 'SEGMENTS': 8, 'DISSOLVE': True, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            holes_rm   = processing.run(
                'native:deleteholes',
                {'INPUT': buf_pos, 'MIN_AREA': 0, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            amalgamated = processing.run(
                'native:buffer',
                {'INPUT': holes_rm, 'DISTANCE': -buff_dist, 'SEGMENTS': 8, 'DISSOLVE': False, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            bridge_raw  = processing.run(
                'native:difference',
                {'INPUT': amalgamated, 'OVERLAY': poly_uid_layer, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            bridge_single_id = processing.run(
                'native:multiparttosingleparts',
                {'INPUT': bridge_raw, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            bridge_single = QgsProcessingUtils.mapLayerFromString(bridge_single_id, context)

            # ------------------------------------------------------------------
            # 6. Validasi jembatan:
            #    (a) Anti-self-aggregation: jembatan harus menyentuh ≥ 2 target pulau
            #    (b) Anti-intersection: jembatan tidak boleh overlap dengan pulau bypass
            # ------------------------------------------------------------------
            feedback.pushInfo('6. Memvalidasi jembatan (anti-self-aggregation + anti-intersection)...')
            orig_idx  = QgsSpatialIndex(poly_uid_layer.getFeatures())
            orig_dict = {f.id(): f for f in poly_uid_layer.getFeatures()}

            valid_bridges_lyr = QgsVectorLayer('Polygon?crs=EPSG:3857', 'Valid_Bridges', 'memory')
            valid_bridges_lyr.dataProvider().addAttributes(uid_fields.toList())
            valid_bridges_lyr.updateFields()
            valid_feats = []
            n_ditolak_selfagr = 0
            n_ditolak_intersect = 0

            for b_feat in bridge_single.getFeatures():
                b_geom = b_feat.geometry()

                # (a) Cek anti-self-aggregation: harus menyentuh ≥ 2 target pulau
                g_buf        = b_geom.buffer(0.01, 3)
                intersecting = orig_idx.intersects(g_buf.boundingBox())
                touched_uids = set()
                for fid in intersecting:
                    if g_buf.intersects(orig_dict[fid].geometry()):
                        touched_uids.add(orig_dict[fid]['uid'])

                if len(touched_uids) < 2:
                    n_ditolak_selfagr += 1
                    continue  # tolak: tidak menghubungkan ≥ 2 pulau target

                # (b) Cek anti-intersection: jembatan tidak boleh intersect pulau bypass
                hits_bypass = bypass_idx.intersects(b_geom.boundingBox())
                collides = False
                for bid in hits_bypass:
                    bp_f = bypass_dict.get(bid)
                    if bp_f is None:
                        continue
                    bp_geom = bp_f.geometry()
                    if not bp_geom.isGeosValid():
                        bp_geom = bp_geom.makeValid()
                    if b_geom.intersects(bp_geom):
                        # Hitung area irisan — toleransi kecil agar tidak gagal karena
                        # menyentuh di titik/garis batas saja (shared edge)
                        try:
                            overlap = b_geom.intersection(bp_geom)
                            if overlap.area() > 1.0:   # > 1 m² = benar-benar overlap
                                collides = True
                                break
                        except Exception:
                            collides = True
                            break

                if collides:
                    n_ditolak_intersect += 1
                    continue  # tolak: jembatan nabrak pulau non-target

                valid_feats.append(b_feat)

            valid_bridges_lyr.dataProvider().addFeatures(valid_feats)
            feedback.pushInfo(f'   Jembatan valid              : {len(valid_feats)}')
            feedback.pushInfo(f'   Ditolak (self-aggregation)  : {n_ditolak_selfagr}')
            feedback.pushInfo(f'   Ditolak (nabrak pulau lain) : {n_ditolak_intersect}')

            # ------------------------------------------------------------------
            # 7. Dissolve dan gabungkan seluruh layer
            # ------------------------------------------------------------------
            feedback.pushInfo('7. Dissolve dan gabungkan seluruh layer...')
            merged_polys    = processing.run(
                'native:mergevectorlayers',
                {'LAYERS': [poly_uid_layer, valid_bridges_lyr], 'CRS': QgsCoordinateReferenceSystem('EPSG:3857'), 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']
            dissolved_polys = processing.run(
                'native:dissolve',
                {'INPUT': merged_polys, 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']

            if is_line:
                lines_boundary   = processing.run(
                    'native:boundary',
                    {'INPUT': dissolved_polys, 'OUTPUT': 'memory:'},
                    context=context, feedback=feedback, is_child_algorithm=True
                )['OUTPUT']
                final_merged_pre = processing.run(
                    'native:mergevectorlayers',
                    {'LAYERS': [lines_boundary, layer_bypass], 'CRS': QgsCoordinateReferenceSystem('EPSG:3857'), 'OUTPUT': 'memory:'},
                    context=context, feedback=feedback, is_child_algorithm=True
                )['OUTPUT']
            else:
                final_merged_pre = processing.run(
                    'native:mergevectorlayers',
                    {'LAYERS': [dissolved_polys, layer_bypass], 'CRS': QgsCoordinateReferenceSystem('EPSG:3857'), 'OUTPUT': 'memory:'},
                    context=context, feedback=feedback, is_child_algorithm=True
                )['OUTPUT']
        else:
            feedback.pushInfo('Tidak ada pulau target. Melewati proses agregasi...')
            final_merged_pre = processing.run(
                'native:mergevectorlayers',
                {'LAYERS': [layer_bypass], 'CRS': QgsCoordinateReferenceSystem('EPSG:3857'), 'OUTPUT': 'memory:'},
                context=context, feedback=feedback, is_child_algorithm=True
            )['OUTPUT']

        # ------------------------------------------------------------------
        # 8. Klasifikasi status agregasi dan reproyeksi ke CRS awal
        # ------------------------------------------------------------------
        feedback.pushInfo('8. Klasifikasi status agregasi dan reproyeksi ke CRS awal...')
        final_parts = processing.run(
            'native:multiparttosingleparts',
            {'INPUT': final_merged_pre, 'OUTPUT': 'memory:'},
            context=context, feedback=feedback, is_child_algorithm=True
        )['OUTPUT']
        final_layer = QgsProcessingUtils.mapLayerFromString(final_parts, context)

        crs_s = QgsCoordinateReferenceSystem('EPSG:3857')
        crs_d = source_layer.sourceCrs()
        trans = QgsCoordinateTransform(crs_s, crs_d, context.project())

        output_fields = QgsFields()
        for field in source_layer.fields():
            output_fields.append(field)
        output_fields.append(QgsField('status_agr', QVariant.String, len=15))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, output_fields, input_wkb_type, crs_d
        )

        poly_uid_idx  = QgsSpatialIndex(poly_uid_layer.getFeatures()) if has_islands else None
        poly_uid_dict = {f.id(): f for f in poly_uid_layer.getFeatures()} if has_islands else {}
        expected_type = QgsWkbTypes.LineGeometry if is_line else QgsWkbTypes.PolygonGeometry

        n_diagregasi = n_tidak = 0

        for feat in final_layer.getFeatures():
            geom = feat.geometry()
            if not geom.isGeosValid():
                geom = geom.makeValid()
            if not geom.isEmpty() and geom.type() == expected_type:
                status_agregasi = 'Tidak'
                if poly_uid_idx:
                    geom_test = geom.buffer(0.1, 3) if is_line else geom
                    uids_tergabung = set()
                    for pid in poly_uid_idx.intersects(geom_test.boundingBox()):
                        orig_poly_f = poly_uid_dict.get(pid)
                        if orig_poly_f and geom_test.intersects(orig_poly_f.geometry()):
                            inter = geom_test.intersection(orig_poly_f.geometry())
                            if inter.area() > 0.1:
                                uids_tergabung.add(orig_poly_f['uid'])
                    if len(uids_tergabung) >= 2:
                        status_agregasi = 'Diagregasi'

                intersecting_ids = proj_idx.intersects(geom.boundingBox())
                best_orig_feat   = None
                max_size         = -1
                for fid in intersecting_ids:
                    orig_f = proj_features.get(fid)
                    if orig_f and geom.intersects(orig_f.geometry()):
                        try:
                            intersection = geom.intersection(orig_f.geometry())
                            size = intersection.length() if is_line else intersection.area()
                            if size > max_size:
                                max_size       = size
                                best_orig_feat = orig_f
                        except Exception:
                            pass

                geom.transform(trans)
                out_feat = QgsFeature(output_fields)
                out_feat.setGeometry(geom)
                if best_orig_feat:
                    for fld in source_layer.fields():
                        out_feat[fld.name()] = best_orig_feat[fld.name()]
                else:
                    for fld in source_layer.fields():
                        try:
                            out_feat[fld.name()] = feat[fld.name()]
                        except Exception:
                            pass
                out_feat['status_agr'] = status_agregasi
                sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

                if status_agregasi == 'Diagregasi':
                    n_diagregasi += 1
                else:
                    n_tidak += 1

        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'CRS output         : {crs_d.authid()}')
        feedback.pushInfo(f'Fitur diagregasi   : {n_diagregasi}')
        feedback.pushInfo(f'Fitur tidak diubah : {n_tidak}')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('Lanjutkan ke Tahap 5 - Eksagerasi.')

        return {self.OUTPUT: dest_id}
