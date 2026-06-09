# =============================================================================
# PENGECEKAN MMU (Minimum Mapping Unit) - Generalisasi Garis Pantai
# =============================================================================
# Toolbox : Generalisasi Garis Pantai
# Tahap   : 1 - Pengecekan MMU
# Input   : Layer garis pantai hasil segmentasi morfologi (EPSG:3857)
#           Skala input dan skala target
# Output  : Layer yang sama dengan 5 kolom tambahan:
#             - luas_m2           : Luas total kelompok topologi (m²), NULL jika terbuka
#             - panjang_m         : Panjang total kelompok topologi (m)
#             - kelas_bentuk      : 'Poligon', 'Garis', atau 'Titik'
#             - keterangan_bentuk : Nilai A, L, W, R hasil perhitungan
#             - pulau_kecil       : 1 jika A < Amin skala target, 0 jika tidak
#
# Logika pengelompokan topologi:
#   Segmen yang ujungnya saling menyentuh (toleransi snap 0.01 m hardcoded)
#   dikelompokkan menjadi satu kesatuan untuk perhitungan dimensi.
#   Atribut morfologi per segmen tetap dipertahankan di masing-masing baris.
#   Nilai panjang_m dan luas_m2 = dimensi TOTAL kelompok, bukan per segmen.
#
# Toleransi snap 0.01 m (hardcoded):
#   Menangani floating point mismatch hasil segmentasi ML tanpa risiko
#   menggabungkan segmen yang memang terpisah secara fisik.
#
# Proyeksi: Input boleh CRS apapun — akan otomatis diproyeksikan ke EPSG:3857
#            sebelum proses. Jika input sudah EPSG:3857, tidak ada reproyeksi.
# Referensi:
#   - Ledermann (2023): minimum lebar simbol 0.5 mm
#   - Topfer & Pillewizer (1966): Radical Law
#   - Petunjuk Teknis Generalisasi BIG
# =============================================================================

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsFeatureSink,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsGeometry,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)
from PyQt5.QtCore import QVariant
from collections import defaultdict


class PengecekanMMU(QgsProcessingAlgorithm):

    INPUT        = 'INPUT'
    SKALA_INPUT  = 'SKALA_INPUT'
    SKALA_TARGET = 'SKALA_TARGET'
    OUTPUT       = 'OUTPUT'

    SNAP_TOL = 0.01  # meter - hardcoded, tidak ditampilkan ke user

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

    MMU_TABLE = {
        5000:    {'wmin': 2.5,   'lmin': 8.0,    'amin': 20.0,       'mmu_luas': 6.25,     'mmu_panjang': 2.5,   'r': 10},
        25000:   {'wmin': 12.5,  'lmin': 40.0,   'amin': 1500.0,     'mmu_luas': 156.25,   'mmu_panjang': 12.5,  'r': 10},
        50000:   {'wmin': 25.0,  'lmin': 80.0,   'amin': 6000.0,     'mmu_luas': 625.0,    'mmu_panjang': 25.0,  'r': 10},
        250000:  {'wmin': 125.0, 'lmin': 400.0,  'amin': 150000.0,   'mmu_luas': 15625.0,  'mmu_panjang': 125.0, 'r': 10},
        500000:  {'wmin': 250.0, 'lmin': 800.0,  'amin': 600000.0,   'mmu_luas': 62500.0,  'mmu_panjang': 250.0, 'r': 10},
        1000000: {'wmin': 500.0, 'lmin': 1600.0, 'amin': 3200000.0,  'mmu_luas': 250000.0, 'mmu_panjang': 500.0, 'r': 10},
    }

    def name(self):
        return 'pengecekan_mmu'

    def displayName(self):
        return 'Tahap 1 - Pengecekan MMU'

    def group(self):
        return 'Generalisasi Garis Pantai'

    def groupId(self):
        return 'generalisasi_garis_pantai'

    def shortHelpString(self):
        return (
            '<b>Tahap 1 - Pengecekan MMU</b><br>'
            '<i>Minimum Mapping Unit — Generalisasi Garis Pantai</i><br><br>'
            'Menghitung dimensi setiap segmen garis pantai dan mengklasifikasikan '
            'bentuknya berdasarkan threshold MMU skala target. Segmen-segmen yang '
            'terhubung secara topologi dihitung sebagai satu kesatuan.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: hasil segmentasi morfologi (CRS apapun — '
            'otomatis diproyeksikan ke EPSG:3857 jika belum)<br>'
            '- <i>Skala Input</i>: skala sumber data<br>'
            '- <i>Skala Target</i>: skala hasil generalisasi yang diinginkan<br><br>'
            '<b>Output — 5 kolom tambahan di attribute table:</b><br>'
            '- <i>luas_m2</i>: luas total kelompok (m2, NULL jika geometri terbuka)<br>'
            '- <i>panjang_m</i>: panjang total kelompok (m)<br>'
            '- <i>kelas_bentuk</i>: Poligon / Garis / Titik<br>'
            '- <i>keterangan_bentuk</i>: nilai A, L, W, R hasil perhitungan<br>'
            '- <i>pulau_kecil</i>: 1 jika A &lt; Amin skala target, 0 jika tidak<br><br>'
            '<b>Threshold per skala target:</b><br>'
            '1:25.000    &nbsp;→ Wmin 12,5 m &nbsp;| Lmin 40 m &nbsp;&nbsp;| Amin 1.500 m2<br>'
            '1:50.000    &nbsp;→ Wmin 25 m &nbsp;&nbsp;&nbsp;| Lmin 80 m &nbsp;&nbsp;| Amin 6.000 m2<br>'
            '1:250.000   &nbsp;→ Wmin 125 m &nbsp;&nbsp;| Lmin 400 m &nbsp;| Amin 150.000 m2<br>'
            '1:500.000   &nbsp;→ Wmin 250 m &nbsp;&nbsp;| Lmin 800 m &nbsp;| Amin 600.000 m2<br>'
            '1:1.000.000 → Wmin 500 m &nbsp;&nbsp;| Lmin 1.600 m | Amin 3.200.000 m2<br><br>'
            '<b>Catatan:</b><br>'
            'Tidak ada fitur yang dihapus di tahap ini. '
            'Keputusan eliminasi dilakukan di Tahap 3 - Seleksi dan Eliminasi.'
        )

    def createInstance(self):
        return PengecekanMMU()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT,
                'Layer Garis Pantai (hasil segmentasi morfologi)',
                [QgsProcessing.TypeVectorLine],
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SKALA_INPUT,
                'Skala Input (skala sumber data)',
                options=self.SKALA_INPUT_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.SKALA_TARGET,
                'Skala Target (skala hasil generalisasi)',
                options=self.SKALA_TARGET_OPTIONS,
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                'Output - Pengecekan MMU',
            )
        )

    def _get_endpoints(self, geom):
        wkb_type = QgsWkbTypes.flatType(geom.wkbType())
        if wkb_type == QgsWkbTypes.LineString:
            pts = geom.asPolyline()
            if len(pts) < 2:
                return None, None
            return pts[0], pts[-1]
        if wkb_type == QgsWkbTypes.MultiLineString:
            parts = geom.asMultiPolyline()
            if not parts:
                return None, None
            return parts[0][0], parts[-1][-1]
        return None, None

    def _pts_equal(self, p1, p2):
        if p1 is None or p2 is None:
            return False
        dx = p1.x() - p2.x()
        dy = p1.y() - p2.y()
        return (dx * dx + dy * dy) <= self.SNAP_TOL * self.SNAP_TOL

    def _bangun_kelompok_topologi(self, all_features, feat_endpoints, feedback):
        parent = {}

        def find(x):
            root = x
            while parent.get(root, root) != root:
                root = parent.get(root, root)
            while parent.get(x, x) != root:
                nxt = parent.get(x, x)
                parent[x] = root
                x = nxt
            return root

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        fids = [f.id() for f in all_features]
        for fid in fids:
            parent[fid] = fid

        n = len(fids)
        feedback.pushInfo(f'Membangun kelompok topologi untuk {n} segmen...')

        for i in range(n):
            if feedback.isCanceled():
                break
            fid_i = fids[i]
            s_i, e_i = feat_endpoints[fid_i]
            if s_i is None:
                continue
            for j in range(i + 1, n):
                fid_j = fids[j]
                s_j, e_j = feat_endpoints[fid_j]
                if s_j is None:
                    continue
                if (self._pts_equal(e_i, s_j) or
                        self._pts_equal(e_i, e_j) or
                        self._pts_equal(s_i, s_j) or
                        self._pts_equal(s_i, e_j)):
                    union(fid_i, fid_j)

        feat_to_group  = {fid: find(fid) for fid in fids}
        group_to_feats = defaultdict(list)
        for fid, gid in feat_to_group.items():
            group_to_feats[gid].append(fid)

        feedback.pushInfo(
            f'Ditemukan {len(group_to_feats)} kelompok topologi dari {n} segmen.'
        )
        return feat_to_group, dict(group_to_feats)

    def _is_group_closed(self, fids, feat_endpoints):
        if len(fids) == 1:
            s, e = feat_endpoints[fids[0]]
            if s is None or e is None:
                return False
            return self._pts_equal(s, e)

        endpoints = []
        for fid in fids:
            s, e = feat_endpoints[fid]
            if s is not None:
                endpoints.append(s)
            if e is not None:
                endpoints.append(e)

        n      = len(endpoints)
        paired = [False] * n
        for i in range(n):
            if paired[i]:
                continue
            for j in range(i + 1, n):
                if not paired[j] and self._pts_equal(endpoints[i], endpoints[j]):
                    paired[i] = True
                    paired[j] = True
                    break
        return all(paired)

    def _hitung_panjang_grup(self, fids, feat_geoms):
        return sum(
            feat_geoms[fid].length()
            for fid in fids
            if feat_geoms.get(fid) and not feat_geoms[fid].isEmpty()
        )

    def _hitung_luas_grup(self, fids, feat_geoms):
        all_points = []
        for fid in fids:
            geom = feat_geoms.get(fid)
            if geom is None or geom.isEmpty():
                continue
            wkb = QgsWkbTypes.flatType(geom.wkbType())
            if wkb == QgsWkbTypes.LineString:
                all_points.extend(geom.asPolyline())
            elif wkb == QgsWkbTypes.MultiLineString:
                for part in geom.asMultiPolyline():
                    all_points.extend(part)
        if len(all_points) < 3:
            return None
        try:
            poly_geom = QgsGeometry.fromPolygonXY([all_points])
            if poly_geom and not poly_geom.isEmpty():
                return poly_geom.area()
        except Exception:
            pass
        return None

    def _hitung_mbr_grup(self, fids, feat_geoms):
        geom_list = [
            feat_geoms[fid] for fid in fids
            if feat_geoms.get(fid) and not feat_geoms[fid].isEmpty()
        ]
        if not geom_list:
            return 0.0, 0.0, 0.0
        merged = geom_list[0]
        for g in geom_list[1:]:
            merged = merged.combine(g)
        try:
            _, _, _, width, height = merged.orientedMinimumBoundingBox()
            w = min(width, height)
            l = max(width, height)
            r = (l / w) if w > 0 else 0.0
            return w, l, r
        except Exception:
            return 0.0, 0.0, 0.0

    def _klasifikasi_bentuk(self, tertutup, luas, panjang, w, r, threshold):
        amin  = threshold['amin']
        lmin  = threshold['lmin']
        wmin  = threshold['wmin']
        r_min = threshold['r']

        if tertutup and luas is not None:
            ket = (
                f'A={round(luas, 2)} m2 | '
                f'L={round(panjang, 2)} m | '
                f'W={round(w, 2)} m | '
                f'R={round(r, 2)}'
            )
        else:
            ket = (
                f'A=NULL (terbuka) | '
                f'L={round(panjang, 2)} m | '
                f'W={round(w, 2)} m | '
                f'R={round(r, 2)}'
            )

        if tertutup and luas is not None:
            if luas >= amin:
                kelas = 'Poligon'
            elif panjang >= lmin and (r >= r_min or w < wmin):
                kelas = 'Garis'
            elif panjang >= lmin and r < r_min:
                kelas = 'Titik'
            else:
                kelas = 'Titik'
        else:
            if panjang >= lmin:
                kelas = 'Garis'
            else:
                kelas = 'Titik'

        return kelas, ket

    def processAlgorithm(self, parameters, context, feedback):

        layer  = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        idx_si = self.parameterAsEnum(parameters, self.SKALA_INPUT, context)
        idx_st = self.parameterAsEnum(parameters, self.SKALA_TARGET, context)

        label_input  = self.SKALA_INPUT_OPTIONS[idx_si]
        label_target = self.SKALA_TARGET_OPTIONS[idx_st]
        val_input    = self.SKALA_VALUE[label_input]
        val_target   = self.SKALA_VALUE[label_target]

        if val_target <= val_input:
            raise QgsProcessingException(
                f'ERROR: Skala target ({label_target}) harus lebih kecil dari '
                f'skala input ({label_input}). '
                f'Generalisasi hanya bisa dari skala besar ke skala kecil. '
                f'Contoh benar: skala input 1:25.000 dan skala target 1:250.000.'
            )

        threshold = self.MMU_TABLE[val_target]

        # ---------------------------------------------------------------
        # AUTO-REPROYEKSI ke EPSG:3857 (jika layer input bukan EPSG:3857)
        # ---------------------------------------------------------------
        CRS_3857 = QgsCoordinateReferenceSystem('EPSG:3857')
        src_crs  = layer.sourceCrs()

        if src_crs.authid() != 'EPSG:3857':
            feedback.pushInfo(
                f'CRS layer input terdeteksi: {src_crs.authid()} ({src_crs.description()}). '
                f'Melakukan reproyeksi otomatis ke EPSG:3857...'
            )
            transform = QgsCoordinateTransform(src_crs, CRS_3857, QgsProject.instance())
            reproj_needed = True
        else:
            feedback.pushInfo('CRS layer input: EPSG:3857. Tidak perlu reproyeksi.')
            transform     = None
            reproj_needed = False

        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 1 - PENGECEKAN MMU | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'CRS input      : {src_crs.authid()}')
        feedback.pushInfo(f'CRS proses     : EPSG:3857')
        feedback.pushInfo(f'Skala input    : {label_input}')
        feedback.pushInfo(f'Skala target   : {label_target}')
        feedback.pushInfo(f'Toleransi snap : {self.SNAP_TOL} m')
        feedback.pushInfo(f'Wmin           : {threshold["wmin"]} m')
        feedback.pushInfo(f'Lmin           : {threshold["lmin"]} m')
        feedback.pushInfo(f'Amin           : {threshold["amin"]} m2')
        feedback.pushInfo(f'MMU Luas       : {threshold["mmu_luas"]} m2')
        feedback.pushInfo(f'MMU Panjang    : {threshold["mmu_panjang"]} m')
        feedback.pushInfo('=' * 60)

        feedback.pushInfo('Memuat fitur...')
        all_features = list(layer.getFeatures())

        # Terapkan reproyeksi ke setiap geometri jika diperlukan
        if reproj_needed:
            feedback.pushInfo(
                f'Menerapkan reproyeksi {src_crs.authid()} → EPSG:3857 '
                f'pada {len(all_features)} fitur...'
            )
            for f in all_features:
                geom = f.geometry()
                if geom and not geom.isEmpty():
                    geom.transform(transform)
                    f.setGeometry(geom)

        feat_geoms     = {f.id(): f.geometry() for f in all_features}
        feat_endpoints = {}
        for f in all_features:
            geom = f.geometry()
            if geom and not geom.isEmpty():
                s, e = self._get_endpoints(geom)
                feat_endpoints[f.id()] = (s, e)
            else:
                feat_endpoints[f.id()] = (None, None)

        feat_to_group, group_to_feats = self._bangun_kelompok_topologi(
            all_features, feat_endpoints, feedback
        )

        feedback.pushInfo('Menghitung dimensi per kelompok...')
        group_stats = {}
        amin = threshold['amin']

        for gid, fids in group_to_feats.items():
            if feedback.isCanceled():
                break

            tertutup        = self._is_group_closed(fids, feat_endpoints)
            panjang         = self._hitung_panjang_grup(fids, feat_geoms)
            luas            = self._hitung_luas_grup(fids, feat_geoms) if tertutup else None
            w, _, r         = self._hitung_mbr_grup(fids, feat_geoms)
            kelas, ket      = self._klasifikasi_bentuk(
                tertutup, luas, panjang, w, r, threshold
            )

            pulau_kecil = 0
            if tertutup and luas is not None:
                pulau_kecil = 1 if luas < amin else 0

            group_stats[gid] = {
                'luas_m2':           round(luas, 4) if luas is not None else None,
                'panjang_m':         round(panjang, 4),
                'kelas_bentuk':      kelas,
                'keterangan_bentuk': ket,
                'pulau_kecil':       pulau_kecil,
            }

        kolom_baru = [
            'luas_m2', 'panjang_m', 'kelas_bentuk',
            'keterangan_bentuk', 'pulau_kecil',
        ]
        fields_out = QgsFields()
        for field in layer.fields():
            if field.name() not in kolom_baru:
                fields_out.append(field)

        fields_out.append(QgsField('luas_m2',           QVariant.Double, len=20, prec=4))
        fields_out.append(QgsField('panjang_m',         QVariant.Double, len=20, prec=4))
        fields_out.append(QgsField('kelas_bentuk',      QVariant.String, len=10))
        fields_out.append(QgsField('keterangan_bentuk', QVariant.String, len=100))
        fields_out.append(QgsField('pulau_kecil',       QVariant.Int))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            fields_out, layer.wkbType(), CRS_3857,  # output selalu EPSG:3857
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        feedback.pushInfo('Menulis output...')
        total = len(all_features)
        step  = 100.0 / total if total > 0 else 0

        count_poligon = count_garis = count_titik = count_pulau = 0

        for i, feat in enumerate(all_features):
            if feedback.isCanceled():
                break

            fid  = feat.id()
            gid  = feat_to_group.get(fid, fid)
            stat = group_stats.get(gid, {
                'luas_m2': None, 'panjang_m': 0.0,
                'kelas_bentuk': 'Titik', 'keterangan_bentuk': '',
                'pulau_kecil': 0,
            })

            attrs_lama = [
                feat[field.name()]
                for field in layer.fields()
                if field.name() not in kolom_baru
            ]
            attrs_baru = attrs_lama + [
                stat['luas_m2'],
                stat['panjang_m'],
                stat['kelas_bentuk'],
                stat['keterangan_bentuk'],
                stat['pulau_kecil'],
            ]

            out_feat = feat
            out_feat.setFields(fields_out)
            out_feat.setAttributes(attrs_baru)
            out_feat.setGeometry(feat.geometry())
            sink.addFeature(out_feat, QgsFeatureSink.FastInsert)

            k = stat['kelas_bentuk']
            if k == 'Poligon':   count_poligon += 1
            elif k == 'Garis':   count_garis   += 1
            else:                count_titik    += 1
            if stat['pulau_kecil'] == 1:
                count_pulau += 1

            feedback.setProgress(int(i * step))

        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'CRS input        : {src_crs.authid()}')
        feedback.pushInfo(f'CRS output       : EPSG:3857')
        feedback.pushInfo(f'Reproyeksi       : {"Ya" if reproj_needed else "Tidak (sudah EPSG:3857)"}')
        feedback.pushInfo(f'Total segmen diproses    : {total}')
        feedback.pushInfo(f'Total kelompok topologi  : {len(group_to_feats)}')
        feedback.pushInfo(f'Kelas Poligon            : {count_poligon} segmen')
        feedback.pushInfo(f'Kelas Garis              : {count_garis} segmen')
        feedback.pushInfo(f'Kelas Titik              : {count_titik} segmen')
        feedback.pushInfo(f'Pulau kecil (= 1)        : {count_pulau} segmen')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(
            'Nilai panjang_m dan luas_m2 adalah dimensi TOTAL kelompok '
            'topologi, bukan per segmen individual. Tidak ada fitur yang '
            'dihapus. Lanjutkan ke Tahap 2 - Reklasifikasi.'
        )

        return {self.OUTPUT: dest_id}
