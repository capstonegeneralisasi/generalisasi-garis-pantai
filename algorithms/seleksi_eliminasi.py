# =============================================================================
# SELEKSI DAN ELIMINASI - Generalisasi Garis Pantai
# =============================================================================
# Toolbox : Generalisasi Garis Pantai
# Tahap   : 3 - Seleksi dan Eliminasi
# Input   : Layer garis pantai hasil Tahap 2 Reklasifikasi
# Output  : Layer garis pantai yang sudah dieliminasi
#           (atribut output identik dengan input, tidak ada kolom tambahan)
#
# Kriteria eliminasi:
#   kelas_bentuk = 'Titik' AND GRI = 0
#   → Tidak memenuhi MMU dan tidak memiliki nama GRI → dieliminasi permanen
#
# Kriteria dipertahankan:
#   kelas_bentuk = 'Titik' AND GRI = 1
#   → Tidak memenuhi MMU namun memiliki nama GRI → lanjut ke Tahap 4 Agregasi
#   kelas_bentuk ≠ 'Titik'
#   → Memenuhi MMU (Garis/Poligon) → lanjut ke tahap generalisasi berikutnya
# =============================================================================

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingException,
    QgsFeature,
)

NILAI_TITIK = 'Titik'


class SeleksiEliminasi(QgsProcessingAlgorithm):

    INPUT       = 'INPUT'
    OUTPUT      = 'OUTPUT'
    FIELD_KELAS = 'FIELD_KELAS'
    FIELD_GRI   = 'FIELD_GRI'

    def name(self):
        return 'seleksi_eliminasi'

    def displayName(self):
        return 'Tahap 3 - Seleksi dan Eliminasi'

    def group(self):
        return 'Generalisasi Garis Pantai'

    def groupId(self):
        return 'generalisasi_garis_pantai'

    def createInstance(self):
        return SeleksiEliminasi()

    def tr(self, string):
        return QCoreApplication.translate('SeleksiEliminasi', string)

    def shortHelpString(self):
        return (
            '<b>Tahap 3 - Seleksi dan Eliminasi</b><br>'
            '<i>Pembuangan Segmen Tidak Layak — Generalisasi Garis Pantai</i><br><br>'
            'Membuang segmen garis pantai yang tidak memenuhi kriteria representasi '
            'kartografis pada skala target berdasarkan hasil Tahap 1 dan Tahap 2.<br><br>'
            '<b>Input:</b><br>'
            '- <i>Layer Garis Pantai</i>: hasil Tahap 2 Reklasifikasi<br>'
            '- <i>Kolom Kelas Bentuk</i>: kolom berisi nilai Titik / Garis / Poligon<br>'
            '- <i>Kolom GRI</i>: kolom berisi nilai 1 (ada nama GRI) atau 0 (tidak)<br><br>'
            '<b>Output:</b><br>'
            'Layer garis pantai hasil seleksi. Atribut identik dengan input, '
            'tidak ada kolom tambahan.<br><br>'
            '<b>Kriteria eliminasi:</b><br>'
            '- Kelas Bentuk = Titik AND GRI = 0 → dieliminasi permanen<br><br>'
            '<b>Kriteria dipertahankan:</b><br>'
            '- Kelas Bentuk = Titik AND GRI = 1 → lanjut ke Tahap 4 Agregasi<br>'
            '- Kelas Bentuk ≠ Titik → lanjut ke tahap generalisasi berikutnya<br><br>'
            '<b>Catatan:</b><br>'
            'GRI = NULL diperlakukan sama dengan GRI = 0 (dieliminasi jika Titik).'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT,
            'Layer Garis Pantai (hasil Tahap 2 Reklasifikasi)',
            [QgsProcessing.TypeVectorAnyGeometry]
        ))
        self.addParameter(QgsProcessingParameterField(
            self.FIELD_KELAS,
            'Kolom Kelas Bentuk (berisi: Titik / Garis / Poligon)',
            defaultValue='kelas_bentuk',
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Any,
            optional=False
        ))
        self.addParameter(QgsProcessingParameterField(
            self.FIELD_GRI,
            'Kolom GRI (1 = memiliki nama GRI, 0 = tidak)',
            defaultValue='GRI',
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Any,
            optional=False
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT,
            'Output - Seleksi dan Eliminasi'
        ))

    def processAlgorithm(self, parameters, context, feedback):

        source      = self.parameterAsSource(parameters, self.INPUT, context)
        field_kelas = self.parameterAsString(parameters, self.FIELD_KELAS, context)
        field_gri   = self.parameterAsString(parameters, self.FIELD_GRI, context)

        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        field_names = [f.name() for f in source.fields()]
        for col in [field_kelas, field_gri]:
            if col not in field_names:
                raise QgsProcessingException(
                    f"Kolom '{col}' tidak ditemukan di layer. "
                    f"Kolom yang tersedia: {', '.join(field_names)}"
                )

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            source.fields(), source.wkbType(), source.sourceCrs()
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        feedback.pushInfo('=' * 60)
        feedback.pushInfo('TAHAP 3 - SELEKSI DAN ELIMINASI | Generalisasi Garis Pantai')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Kolom kelas bentuk : {field_kelas}')
        feedback.pushInfo(f'Kolom GRI          : {field_gri}')
        feedback.pushInfo('Kriteria eliminasi : kelas_bentuk = Titik AND GRI = 0')
        feedback.pushInfo('=' * 60)

        n_total = n_dipertahankan = n_dieliminasi = 0
        n_titik_gri1 = n_titik_gri0 = n_titik_gri_null = n_titik_gri_invalid = n_bukan_titik = 0
        total = source.featureCount()

        for i, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(i / total * 100))
            n_total += 1

            raw_kelas = feat[field_kelas]
            raw_gri   = feat[field_gri]
            kelas     = str(raw_kelas).strip() if raw_kelas is not None else ''

            if raw_gri is None:
                gri = None
            else:
                try:
                    gri = int(raw_gri)
                except (TypeError, ValueError):
                    gri = -1

            is_titik     = (kelas == NILAI_TITIK)
            is_gri_tidak = (gri == 0 or gri is None)
            is_gri_ada   = (gri == 1)

            if is_titik and is_gri_tidak:
                n_dieliminasi += 1
                if gri is None:
                    n_titik_gri_null += 1
                else:
                    n_titik_gri0 += 1
                continue

            out_feat = QgsFeature(source.fields())
            out_feat.setGeometry(feat.geometry())
            for field in source.fields():
                out_feat.setAttribute(field.name(), feat[field.name()])
            sink.addFeature(out_feat)
            n_dipertahankan += 1

            if is_titik and is_gri_ada:
                n_titik_gri1 += 1
            elif is_titik and gri == -1:
                n_titik_gri_invalid += 1
            else:
                n_bukan_titik += 1

        pct_elim = round(n_dieliminasi / n_total * 100, 1) if n_total else 0

        feedback.pushInfo('')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo('RINGKASAN HASIL')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(f'Total fitur input          : {n_total}')
        feedback.pushInfo(f'Dipertahankan              : {n_dipertahankan}')
        feedback.pushInfo(f'  Memenuhi MMU             : {n_bukan_titik}')
        feedback.pushInfo(f'  Titik, ada nama GRI      : {n_titik_gri1}')
        if n_titik_gri_invalid > 0:
            feedback.pushInfo(f'  Titik, GRI tidak valid   : {n_titik_gri_invalid} (periksa data!)')
        feedback.pushInfo(f'Dieliminasi                : {n_dieliminasi} ({pct_elim}%)')
        feedback.pushInfo(f'  Titik, GRI = 0           : {n_titik_gri0}')
        if n_titik_gri_null > 0:
            feedback.pushInfo(f'  Titik, GRI = NULL        : {n_titik_gri_null}')
        feedback.pushInfo('=' * 60)
        feedback.pushInfo(
            'Lanjutkan ke Tahap 4 - Agregasi.'
        )

        return {self.OUTPUT: dest_id}
