class ScannerReader:
    def __init__(self, prefix: str = "", suffix: str = "", require_enter: bool = False):
        self.prefix = prefix
        self.suffix = suffix
        self.require_enter = require_enter

    def process(self, text: str) -> str:
        text = text.strip()
        if text.startswith(self.prefix):
            text = text[len(self.prefix):]
        if self.suffix and text.endswith(self.suffix):
            text = text[:-len(self.suffix)]
        return text
