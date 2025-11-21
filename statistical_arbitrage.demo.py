import requests
from decimal import Decimal, getcontext
import time

getcontext().prec = 18

# -----------------------
# Config - BUY PULLBACKS IN UPTRENDS
# -----------------------
TOKENS = ["ETH", "MATIC", "BTC"]
INITIAL_CAPITAL_USD = Decimal("100")

# Trend identification
MIN_7D_RETURN = Decimal("0.03")  # Need 3%+ over 7 days to confirm uptrend
MIN_VOLUME_RATIO = Decimal("1.1")  # Volume 10% above average

# Pullback entry (buy dips in uptrends)
PULLBACK_MIN = Decimal("-0.02")  # Price pulled back 2-4%
PULLBACK_MAX = Decimal("-0.04")
RSI_OVERSOLD = 40  # RSI < 40 on pullback

# Exit rules
TAKE_PROFIT = Decimal("0.12")  # 12% profit target
STOP_LOSS = Decimal("0.05")  # 5% stop loss
TRAILING_STOP = Decimal("0.03")  # 3% trailing stop after profit
MAX_HOLD_DAYS = 7  # Exit after 7 days regardless

# Costs
SLIPPAGE = Decimal("0.002")
GAS_FEE_USD = Decimal("0.01")

# -----------------------
# Data Functions
# -----------------------
def fetch_daily_data(token_symbol, days=90):
    """Fetch daily OHLCV data"""
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {"fsym": token_symbol, "tsym": "USD", "limit": days}
    
    resp = requests.get(url, params=params, timeout=10).json()
    if resp.get("Response") == "Error":
        raise ValueError(f"API Error for {token_symbol}")
    
    data = resp["Data"]["Data"]
    closes = [Decimal(str(d["close"])) for d in data]
    highs = [Decimal(str(d["high"])) for d in data]
    lows = [Decimal(str(d["low"])) for d in data]
    volumes = [Decimal(str(d["volumeto"])) for d in data]
    
    return closes, highs, lows, volumes

# -----------------------
# Technical Analysis
# -----------------------
def calculate_sma(prices, period):
    """Simple Moving Average"""
    if len(prices) < period:
        return prices[-1]
    return sum(prices[-period:]) / period

def calculate_rsi(prices, period=14):
    """RSI indicator"""
    if len(prices) < period + 1:
        return 50
    
    gains = []
    losses = []
    for i in range(-period, 0):
        change = prices[i] - prices[i-1]
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))

def is_in_uptrend(prices, highs, volumes):
    """
    Confirm uptrend:
    - 7-day return positive
    - Price above 20-day SMA
    - Making higher highs
    - Volume healthy
    """
    if len(prices) < 20:
        return False
    
    # Check 7-day return
    return_7d = (prices[-1] - prices[-8]) / prices[-8]
    if return_7d < MIN_7D_RETURN:
        return False
    
    # Check SMA
    sma_20 = calculate_sma(prices, 20)
    if prices[-1] < sma_20:
        return False
    
    # Check higher highs
    recent_high = max(highs[-7:])
    older_high = max(highs[-14:-7])
    if recent_high <= older_high:
        return False
    
    # Check volume
    recent_vol = sum(volumes[-7:]) / 7
    avg_vol = sum(volumes[-30:]) / 30
    if recent_vol < avg_vol * MIN_VOLUME_RATIO:
        return False
    
    return True

def is_pullback_entry(prices, highs, rsi):
    """
    Detect pullback entry opportunity:
    - Recent high was made
    - Price pulled back 2-4% from high
    - RSI oversold
    - Not breaking support
    """
    if len(prices) < 10:
        return False, 0
    
    # Find recent high (last 5 days)
    recent_high = max(highs[-5:])
    current_price = prices[-1]
    
    # Calculate pullback percentage
    pullback_pct = (current_price - recent_high) / recent_high
    
    # Check if in pullback range
    if pullback_pct > PULLBACK_MIN or pullback_pct < PULLBACK_MAX:
        return False, 0
    
    # Check RSI
    if rsi > RSI_OVERSOLD:
        return False, 0
    
    # Check support - price shouldn't be below 20-day low
    low_20 = min(prices[-20:])
    if current_price < low_20 * Decimal("1.02"):  # Within 2% of 20-day low
        return False, 0
    
    # Calculate entry strength (how good is this pullback)
    strength = abs(float(pullback_pct)) + (RSI_OVERSOLD - rsi) / 10
    
    return True, strength

# -----------------------
# Trading Functions
# -----------------------
def execute_swap(from_amount, from_price, to_price):
    """Execute swap"""
    usd_value = from_amount * from_price
    usd_after_costs = (usd_value - GAS_FEE_USD) * (Decimal("1") - SLIPPAGE)
    
    if usd_after_costs <= 0:
        return Decimal("0")
    
    return usd_after_costs / to_price

# -----------------------
# Main Strategy
# -----------------------
def pullback_strategy():
    """
    Strategy: Buy pullbacks in established uptrends
    
    Entry: Token in uptrend + pulls back 2-4% + RSI < 40
    Exit: +12% profit OR -5% stop OR trend breaks
    
    This catches trends early (on dips) rather than late (on breakouts)
    """
    
    print("\n" + "="*80)
    print("FETCHING DATA")
    print("="*80)
    
    # Fetch data
    token_data = {}
    for token in TOKENS:
        closes, highs, lows, volumes = fetch_daily_data(token, days=90)
        token_data[token] = {
            'closes': closes,
            'highs': highs,
            'lows': lows,
            'volumes': volumes
        }
        print(f"✓ Fetched {len(closes)} days for {token}")
        time.sleep(0.3)
    
    # Initialize
    holdings = {"USDT": INITIAL_CAPITAL_USD}
    for token in TOKENS:
        holdings[token] = Decimal("0")
    
    position = None
    
    stats = {
        'trades': 0,
        'wins': 0,
        'losses': 0,
        'gas_paid': Decimal("0"),
        'max_value': INITIAL_CAPITAL_USD
    }
    
    print(f"\n{'='*80}")
    print(f"PULLBACK STRATEGY - BUY DIPS IN UPTRENDS")
    print(f"{'='*80}")
    print(f"Initial Capital: ${INITIAL_CAPITAL_USD}")
    print(f"Entry: Uptrend + Pullback (2-4%) + RSI < {RSI_OVERSOLD}")
    print(f"Exit: +{float(TAKE_PROFIT)*100:.0f}% profit | -{float(STOP_LOSS)*100:.0f}% stop | {MAX_HOLD_DAYS}d max")
    print(f"{'='*80}\n")
    
    num_days = len(token_data[TOKENS[0]]['closes'])
    
    for day in range(30, num_days):  # Start after 30 days for good data
        
        # Get current prices
        current_prices = {token: token_data[token]['closes'][day] for token in TOKENS}
        
        # Calculate portfolio value
        portfolio_value = holdings["USDT"]
        for token in TOKENS:
            portfolio_value += holdings[token] * current_prices[token]
        
        stats['max_value'] = max(stats['max_value'], portfolio_value)
        
        # If holding position, check exit
        if position:
            token = position['token']
            entry_price = position['entry_price']
            entry_value = position['entry_value']
            current_price = current_prices[token]
            
            # Update peak
            current_value = holdings[token] * current_price
            if current_value > position['peak_value']:
                position['peak_value'] = current_value
            
            pnl = current_value - entry_value
            pnl_pct = pnl / entry_value
            
            # Trailing stop P&L
            trailing_pnl = (current_value - position['peak_value']) / position['peak_value']
            
            days_held = day - position['entry_day']
            
            should_exit = False
            exit_reason = ""
            
            # Take profit
            if pnl_pct >= TAKE_PROFIT:
                should_exit = True
                exit_reason = f"TAKE PROFIT (+{float(pnl_pct)*100:.1f}%)"
            
            # Stop loss
            elif pnl_pct <= -STOP_LOSS:
                should_exit = True
                exit_reason = f"STOP LOSS ({float(pnl_pct)*100:.1f}%)"
            
            # Trailing stop (after some profit)
            elif pnl_pct > 0.03 and trailing_pnl <= -TRAILING_STOP:
                should_exit = True
                exit_reason = f"TRAILING STOP (+{float(pnl_pct)*100:.1f}%)"
            
            # Time stop
            elif days_held >= MAX_HOLD_DAYS:
                should_exit = True
                exit_reason = f"TIME STOP ({days_held}d, {float(pnl_pct)*100:+.1f}%)"
            
            # Trend reversal
            elif days_held >= 2:
                closes = token_data[token]['closes'][:day+1]
                highs = token_data[token]['highs'][:day+1]
                volumes = token_data[token]['volumes'][:day+1]
                
                still_uptrend = is_in_uptrend(closes, highs, volumes)
                if not still_uptrend:
                    should_exit = True
                    exit_reason = f"TREND BROKEN ({float(pnl_pct)*100:+.1f}%)"
            
            if should_exit:
                # Exit
                amount_received = execute_swap(
                    holdings[token],
                    current_price,
                    Decimal("1")
                )
                
                profit = amount_received - entry_value
                holdings[token] = Decimal("0")
                holdings["USDT"] = amount_received
                stats['gas_paid'] += GAS_FEE_USD
                stats['trades'] += 1
                
                if profit > 0:
                    stats['wins'] += 1
                else:
                    stats['losses'] += 1
                
                print(f"Day {day}: {exit_reason}")
                print(f"  EXIT {token}: ${entry_price:.2f} → ${current_price:.2f}")
                print(f"  Held {days_held}d | P&L: ${profit:.2f} ({float(pnl_pct)*100:+.1f}%)")
                print(f"  Portfolio: ${portfolio_value:.2f}\n")
                
                position = None
        
        # If no position, look for pullback entries
        if not position:
            opportunities = []
            
            for token in TOKENS:
                closes = token_data[token]['closes'][:day+1]
                highs = token_data[token]['highs'][:day+1]
                lows = token_data[token]['lows'][:day+1]
                volumes = token_data[token]['volumes'][:day+1]
                
                # Check if in uptrend
                in_uptrend = is_in_uptrend(closes, highs, volumes)
                
                if in_uptrend:
                    # Check for pullback entry
                    rsi = calculate_rsi(closes, 14)
                    is_pullback, strength = is_pullback_entry(closes, highs, rsi)
                    
                    if is_pullback:
                        recent_high = max(highs[-5:])
                        pullback_pct = (closes[-1] - recent_high) / recent_high
                        
                        opportunities.append({
                            'token': token,
                            'strength': strength,
                            'rsi': rsi,
                            'pullback_pct': pullback_pct,
                            'price': current_prices[token]
                        })
            
            # Take best opportunity
            if opportunities:
                best = max(opportunities, key=lambda x: x['strength'])
                token = best['token']
                
                # Enter position
                invest_amount = holdings["USDT"] * Decimal("0.95")
                amount_received = execute_swap(
                    invest_amount,
                    Decimal("1"),
                    best['price']
                )
                
                if amount_received > 0:
                    entry_value = invest_amount - GAS_FEE_USD - (invest_amount * SLIPPAGE)
                    holdings["USDT"] -= invest_amount
                    holdings[token] = amount_received
                    stats['gas_paid'] += GAS_FEE_USD
                    stats['trades'] += 1
                    
                    position = {
                        'token': token,
                        'entry_day': day,
                        'entry_price': best['price'],
                        'entry_value': entry_value,
                        'peak_value': entry_value
                    }
                    
                    print(f"Day {day}: BUY PULLBACK - {token}")
                    print(f"  Pullback: {float(best['pullback_pct'])*100:.1f}% from recent high")
                    print(f"  RSI: {best['rsi']:.0f} (oversold)")
                    print(f"  Strength: {best['strength']:.2f}")
                    print(f"  Price: ${best['price']:.2f} | Invested: ${invest_amount:.2f}")
                    print(f"  Portfolio: ${portfolio_value:.2f}\n")
    
    # FINAL RESULTS
    print(f"\n{'='*80}")
    print(f"FINAL RESULTS")
    print(f"{'='*80}")
    
    final_prices = {token: token_data[token]['closes'][-1] for token in TOKENS}
    final_value = holdings["USDT"]
    
    print(f"\nFinal Holdings:")
    print(f"  USDT: ${holdings['USDT']:.2f}")
    for token in TOKENS:
        if holdings[token] > 0:
            value = holdings[token] * final_prices[token]
            final_value += value
            print(f"  {token}: {holdings[token]:.8f} (${value:.2f})")
    
    profit = final_value - INITIAL_CAPITAL_USD
    roi = (profit / INITIAL_CAPITAL_USD) * 100
    
    print(f"\n{'='*80}")
    print(f"Performance:")
    print(f"  Initial: ${INITIAL_CAPITAL_USD:.2f}")
    print(f"  Peak: ${stats['max_value']:.2f}")
    print(f"  Final: ${final_value:.2f}")
    print(f"  P&L: ${profit:.2f} ({float(roi):+.2f}%)")
    print(f"\nStats:")
    print(f"  Trades: {stats['trades']} | Wins: {stats['wins']} | Losses: {stats['losses']}")
    if stats['trades'] > 0:
        print(f"  Win Rate: {(stats['wins']/stats['trades'])*100:.1f}%")
    print(f"  Gas: ${stats['gas_paid']:.2f}")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    try:
        pullback_strategy()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()