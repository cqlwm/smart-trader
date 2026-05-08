from strategies.smc.models import PremiumDiscount, TrailingExtremes, ZonePosition


def compute_premium_discount(
    trailing: TrailingExtremes,
    current_price: float,
) -> PremiumDiscount:
    top = trailing.top
    bottom = trailing.bottom

    premium_zone_high = top
    premium_zone_low = 0.95 * top + 0.05 * bottom
    equilibrium = (top + bottom) / 2
    discount_zone_high = 0.95 * bottom + 0.05 * top
    discount_zone_low = bottom

    if current_price > premium_zone_low:
        position = ZonePosition.PREMIUM
    elif current_price < discount_zone_high:
        position = ZonePosition.DISCOUNT
    else:
        position = ZonePosition.EQUILIBRIUM

    return PremiumDiscount(
        premium_zone_high=premium_zone_high,
        premium_zone_low=premium_zone_low,
        equilibrium=equilibrium,
        discount_zone_high=discount_zone_high,
        discount_zone_low=discount_zone_low,
        current_position=position,
    )
