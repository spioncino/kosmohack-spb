from evarisk.sources.registry import adapters


def test_registry_names_match_adapters() -> None:
    reg = adapters()
    for name, adapter in reg.items():
        assert adapter.name == name
        assert adapter.products
        assert isinstance(adapter.supports_archive, bool)
