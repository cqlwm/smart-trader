from strategy import Signal

class NoneSignal(Signal):
    def __init__(self, side):
        super().__init__(side)

    def run(self, df):
        return 1
    
    def is_entry(self, df) -> bool:
        return True

    def is_exit(self, df) -> bool:
        return True