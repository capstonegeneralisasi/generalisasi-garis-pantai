from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
import os
from .algorithms.pengecekan_mmu    import PengecekanMMU
from .algorithms.reklasifikasi     import Reklasifikasi
from .algorithms.seleksi_eliminasi import SeleksiEliminasi
from .algorithms.agregasi          import Agregasi
from .algorithms.eksagerasi        import Eksagerasi
from .algorithms.simplifikasi      import Simplifikasi
from .algorithms.smoothing         import Smoothing

class GeneralisasiGarisPantaiProvider(QgsProcessingProvider):
    def __init__(self): super().__init__()
    def id(self):       return 'generalisasi_garis_pantai'
    def name(self):     return 'Generalisasi Garis Pantai'
    def longName(self): return 'Generalisasi Garis Pantai — Tim Capstone ITB 2026'
    def icon(self):
        p = os.path.join(os.path.dirname(__file__), 'icon.png')
        return QIcon(p) if os.path.exists(p) else super().icon()
    def loadAlgorithms(self):
        for alg in [PengecekanMMU(), Reklasifikasi(), SeleksiEliminasi(),
                    Agregasi(), Eksagerasi(), Simplifikasi(), Smoothing()]:
            self.addAlgorithm(alg)
    def versionInfo(self): return '1.3.0'
