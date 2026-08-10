class PortfolioError(ValueError):
    pass


class PortfolioNotFoundError(PortfolioError):
    pass


class InsufficientBalanceError(PortfolioError):
    pass
