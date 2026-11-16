import os

__version__ = "0.1.1"

# Settings were LEADCOMMONS_* before the rename to Pépite: keep honouring them.
for _key, _value in list(os.environ.items()):
    if _key.startswith("LEADCOMMONS_"):
        os.environ.setdefault("PEPITE_" + _key.removeprefix("LEADCOMMONS_"), _value)
