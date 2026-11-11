from collections.abc import Callable

from evarisk.sources.base import SourceAdapter
from evarisk.sources.celestrak_gp import CelestrakGpAdapter
from evarisk.sources.donki import DonkiAdapter
from evarisk.sources.gfz_kp import GfzKpAdapter
from evarisk.sources.meteor_docs import MeteorDocsAdapter
from evarisk.sources.nasa_oem import NasaOemAdapter
from evarisk.sources.socrates import SocratesAdapter
from evarisk.sources.swpc_live import SwpcLiveAdapter

_FACTORIES: dict[str, Callable[[], SourceAdapter]] = {
    "nasa_oem": NasaOemAdapter,
    "celestrak": CelestrakGpAdapter,
    "swpc_live": SwpcLiveAdapter,
    "donki": DonkiAdapter,
    "gfz_kp": GfzKpAdapter,
    "socrates": SocratesAdapter,
    "meteor_docs": MeteorDocsAdapter,
}


def adapters() -> dict[str, SourceAdapter]:
    return {name: factory() for name, factory in _FACTORIES.items()}
