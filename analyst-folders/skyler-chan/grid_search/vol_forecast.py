"""
Volatility Forecasting Module - HAR-RV Model
=============================================
Implements the Heterogeneous Autoregressive Realized Volatility model
(Corsi, 2009) using Parkinson range-based variance estimator.

Used to predict when realized vol will exceed implied vol,
enabling conditional gamma scalping (only enter when forecast RV > IV).
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Tuple
import yfinance as yf
from sklearn.linear_model import LinearRegression
import warnings
warnings.filterwarnings('ignore')


class VolForecaster:
    """
    HAR-RV volatility forecaster with regime filters.

    Predicts N-day-ahead realized volatility using:
    - Parkinson range-based variance (daily OHLC, no intraday needed)
    - HAR model: RV_forecast = b0 + b_d*RV_daily + b_w*RV_weekly + b_m*RV_monthly
    - Rolling 252-day training window

    Regime filters:
    - VIX term structure slope (contango vs backwardation)
    - RV momentum (5d RV vs 20d RV)
    """

    def __init__(self, symbol: str = 'SPY', lookback_days: int = 900,
                 train_window: int = 252, forecast_horizon: int = 3):
        """
        Args:
            symbol: underlying ticker
            lookback_days: calendar days of history to fetch
            train_window: rolling OLS training window (trading days)
            forecast_horizon: days ahead to forecast (match DTE)
        """
        self.symbol = symbol
        self.lookback_days = lookback_days
        self.train_window = train_window
        self.forecast_horizon = forecast_horizon
        self.ohlc: Optional[pd.DataFrame] = None
        self.vix_data: Optional[pd.DataFrame] = None
        self.rv_series: Optional[pd.Series] = None

    # ------------------------------------------------------------------
    # DATA
    # ------------------------------------------------------------------

    def fetch_data(self, end_date: str = '2025-01-01'):
        """Fetch OHLC for symbol + VIX + VIX3M for regime filters."""
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        start_dt = end_dt - timedelta(days=self.lookback_days)

        print(f"Fetching {self.symbol} OHLC from {start_dt.date()} to {end_dt.date()}...")
        ticker = yf.Ticker(self.symbol)
        df = ticker.history(start=start_dt, end=end_dt)
        if df.empty:
            raise ValueError(f"No data for {self.symbol}")
        df.index = pd.to_datetime(df.index).tz_localize(None)
        self.ohlc = df[['Open', 'High', 'Low', 'Close']].copy()
        print(f"  Got {len(self.ohlc)} trading days")

        # VIX for regime filter
        print("Fetching VIX data for regime filters...")
        try:
            vix = yf.Ticker('^VIX').history(start=start_dt, end=end_dt)
            vix.index = pd.to_datetime(vix.index).tz_localize(None)
            self.vix_data = vix[['Close']].rename(columns={'Close': 'VIX'})

            # VIX3M (3-month VIX) for term structure
            vix3m = yf.Ticker('^VIX3M').history(start=start_dt, end=end_dt)
            if not vix3m.empty:
                vix3m.index = pd.to_datetime(vix3m.index).tz_localize(None)
                self.vix_data['VIX3M'] = vix3m['Close']
            else:
                # Fallback: use 20-day rolling VIX as proxy for longer-term
                self.vix_data['VIX3M'] = self.vix_data['VIX'].rolling(20).mean()
            print(f"  Got {len(self.vix_data)} VIX observations")
        except Exception as e:
            print(f"  VIX fetch failed: {e}. Regime filters disabled.")
            self.vix_data = None

    # ------------------------------------------------------------------
    # REALIZED VOLATILITY ESTIMATION
    # ------------------------------------------------------------------

    def compute_realized_vol(self) -> pd.Series:
        """
        Close-to-close realized volatility estimator.
        Uses rolling N-day window of log returns, annualized.

        This is more comparable to VIX (which is also based on expected
        return variance, not range). Parkinson underestimates by ~30-40%
        relative to VIX because it measures different things.

        Returns annualized realized volatility (as %) using 5-day window.
        """
        df = self.ohlc
        log_ret = np.log(df['Close'] / df['Close'].shift(1))

        # 5-day rolling realized vol (annualized)
        # Using 5-day to be responsive to recent vol changes
        rv_5d = log_ret.rolling(5).std() * np.sqrt(252) * 100

        # Also compute longer windows for HAR components
        self.rv_1d = log_ret.abs() * np.sqrt(252) * 100  # single-day proxy
        self.rv_5d = rv_5d
        self.rv_22d = log_ret.rolling(22).std() * np.sqrt(252) * 100

        # Primary series: use 5-day as the default
        self.rv_series = rv_5d
        return rv_5d

    def compute_har_features(self) -> pd.DataFrame:
        """
        Build HAR feature matrix using close-to-close realized vol:
        - RV_d: 1-day realized vol proxy (yesterday's |return| annualized)
        - RV_w: 5-day rolling realized vol
        - RV_m: 22-day rolling realized vol
        - RV_target: forward N-day realized vol (what we're predicting)
        """
        if self.rv_series is None:
            self.compute_realized_vol()

        h = self.forecast_horizon
        log_ret = np.log(self.ohlc['Close'] / self.ohlc['Close'].shift(1))

        features = pd.DataFrame(index=self.ohlc.index)

        # HAR components (all lagged by 1 day - we can't see today's close yet)
        features['RV_d'] = self.rv_1d.shift(1)                 # yesterday's |return| annualized
        features['RV_w'] = self.rv_5d.shift(1)                  # 5-day rolling vol
        features['RV_m'] = self.rv_22d.shift(1)                 # 22-day rolling vol

        # Target: forward h-day realized vol
        features['RV_target'] = log_ret.rolling(h).std().shift(-h + 1) * np.sqrt(252) * 100

        # Leverage effect: vol increases more after down days
        features['neg_ret'] = (log_ret.shift(1) < 0).astype(float)
        features['abs_ret'] = log_ret.shift(1).abs()
        features['leverage'] = features['neg_ret'] * features['abs_ret']

        return features.dropna()

    # ------------------------------------------------------------------
    # HAR MODEL
    # ------------------------------------------------------------------

    def fit_and_forecast(self, as_of_date: datetime) -> dict:
        """
        Fit HAR model on rolling window ending at as_of_date,
        produce a forecast for the next `forecast_horizon` days.

        Returns:
            {
                'forecast_rv': float (annualized vol %),
                'current_iv': float (VIX as proxy, %),
                'rv_minus_iv': float (edge signal),
                'vix_slope': float (term structure signal),
                'rv_momentum': float (5d RV / 20d RV),
                'should_trade': bool,
                'confidence': str,
            }
        """
        features = self.compute_har_features()

        # Find training window ending at as_of_date
        mask = features.index <= pd.Timestamp(as_of_date)
        available = features[mask]

        if len(available) < self.train_window + 10:
            return self._empty_signal(as_of_date, 'insufficient_data')

        train = available.iloc[-self.train_window:]
        X_cols = ['RV_d', 'RV_w', 'RV_m', 'leverage']

        X_train = train[X_cols].values
        y_train = train['RV_target'].values

        # Fit OLS
        model = LinearRegression()
        model.fit(X_train, y_train)

        # Forecast using the latest available features
        latest = available.iloc[-1]
        X_pred = latest[X_cols].values.reshape(1, -1)
        forecast_rv = model.predict(X_pred)[0]

        # Clamp to reasonable range
        forecast_rv = max(5.0, min(forecast_rv, 100.0))

        # Current IV proxy: use VIX (or last available)
        current_iv = self._get_iv(as_of_date)

        # Regime signals
        vix_slope = self._get_vix_slope(as_of_date)
        rv_momentum = self._get_rv_momentum(as_of_date)

        # Edge = forecast RV minus current IV
        edge = forecast_rv - current_iv

        # VRP z-score and percentiles
        vrp_zscore = self._get_vrp_zscore(as_of_date, forecast_rv, current_iv)
        rv_percentile = self._get_rv_percentile(as_of_date, forecast_rv)
        iv_percentile = self._get_iv_percentile(as_of_date)

        # Decision logic
        should_trade, confidence = self._decide(
            edge, vix_slope, rv_momentum,
            vrp_zscore, rv_percentile, iv_percentile
        )

        return {
            'date': as_of_date,
            'forecast_rv': round(forecast_rv, 2),
            'current_iv': round(current_iv, 2),
            'rv_minus_iv': round(edge, 2),
            'vrp_zscore': round(vrp_zscore, 2) if vrp_zscore is not None else None,
            'rv_percentile': round(rv_percentile, 2) if rv_percentile is not None else None,
            'vix_slope': round(vix_slope, 4) if vix_slope is not None else None,
            'rv_momentum': round(rv_momentum, 4) if rv_momentum is not None else None,
            'should_trade': should_trade,
            'confidence': confidence,
            'model_r2': round(model.score(X_train, y_train), 3),
        }

    def _decide(self, edge: float, vix_slope: Optional[float],
                rv_momentum: Optional[float],
                vrp_zscore: Optional[float] = None,
                rv_percentile: Optional[float] = None,
                iv_percentile: Optional[float] = None) -> Tuple[bool, str]:
        """
        Decision framework — VRP-relative.

        The VRP (Variance Risk Premium) means IV > RV ~80% of the time on SPY
        by ~5-10 vol points. Absolute comparison (RV > IV) almost never happens.

        Instead, we use RELATIVE signals:
        1. VRP z-score: is the current VRP narrower than its recent average?
           (i.e., is RV running hotter than usual relative to IV?)
        2. RV momentum: is realized vol accelerating (5d > 22d)?
        3. VIX term structure: backwardation signals stress/high RV regime
        4. RV percentile: is forecast RV high relative to recent history?

        We trade when multiple signals agree that RV is elevated/rising
        relative to where it usually sits vs IV.
        """
        score = 0

        # Signal 1: VRP is compressed (RV closer to IV than usual)
        # vrp_zscore < -0.5 means VRP is 0.5 std below its 60-day average
        if vrp_zscore is not None and vrp_zscore < -0.5:
            score += 2
        elif vrp_zscore is not None and vrp_zscore < 0:
            score += 1

        # Signal 2: RV momentum (vol accelerating)
        if rv_momentum is not None and rv_momentum > 1.2:
            score += 2
        elif rv_momentum is not None and rv_momentum > 1.1:
            score += 1

        # Signal 3: VIX backwardation (stress regime)
        if vix_slope is not None and vix_slope < -0.05:
            score += 2
        elif vix_slope is not None and vix_slope < -0.02:
            score += 1

        # Signal 4: RV is in upper quartile of recent distribution
        if rv_percentile is not None and rv_percentile > 0.75:
            score += 1

        # Signal 5: Absolute edge — rare but high conviction
        if edge >= 0:
            score += 3
        elif edge >= -3:
            score += 1

        if score >= 4:
            return True, 'STRONG'
        elif score >= 3:
            return True, 'MODERATE'
        else:
            return False, 'NO_TRADE'

    # ------------------------------------------------------------------
    # REGIME HELPERS
    # ------------------------------------------------------------------

    def _get_iv(self, as_of_date: datetime) -> float:
        """Get current implied vol (VIX as proxy) for a date."""
        if self.vix_data is None:
            return 16.0  # fallback
        mask = self.vix_data.index <= pd.Timestamp(as_of_date)
        if mask.sum() == 0:
            return 16.0
        return self.vix_data.loc[mask, 'VIX'].iloc[-1]

    def _get_vix_slope(self, as_of_date: datetime) -> Optional[float]:
        """VIX term structure slope: (VIX3M - VIX) / VIX. Negative = backwardation."""
        if self.vix_data is None or 'VIX3M' not in self.vix_data.columns:
            return None
        mask = self.vix_data.index <= pd.Timestamp(as_of_date)
        if mask.sum() == 0:
            return None
        row = self.vix_data.loc[mask].iloc[-1]
        if pd.isna(row.get('VIX3M')) or row['VIX'] == 0:
            return None
        return (row['VIX3M'] - row['VIX']) / row['VIX']

    def _get_rv_momentum(self, as_of_date: datetime) -> Optional[float]:
        """RV momentum: 5d RV / 22d RV. >1 = vol accelerating."""
        if self.rv_5d is None or self.rv_22d is None:
            return None
        mask = self.rv_5d.index <= pd.Timestamp(as_of_date)
        rv5 = self.rv_5d[mask]
        rv22 = self.rv_22d[mask]
        if len(rv5) < 1 or len(rv22) < 1:
            return None
        val_5 = rv5.iloc[-1]
        val_22 = rv22.iloc[-1]
        if pd.isna(val_5) or pd.isna(val_22) or val_22 == 0:
            return None
        return val_5 / val_22

    def _get_vrp_zscore(self, as_of_date: datetime,
                        forecast_rv: float, current_iv: float) -> Optional[float]:
        """
        Z-score of current VRP (IV - RV) vs its 60-day rolling distribution.
        Negative z-score = VRP is compressed (favorable for long gamma).
        """
        if self.vix_data is None or self.rv_series is None:
            return None

        mask_rv = self.rv_series.index <= pd.Timestamp(as_of_date)
        mask_vix = self.vix_data.index <= pd.Timestamp(as_of_date)
        rv_hist = self.rv_series[mask_rv]
        vix_hist = self.vix_data.loc[mask_vix, 'VIX']

        if len(rv_hist) < 60 or len(vix_hist) < 60:
            return None

        # Align on dates
        combined = pd.DataFrame({'rv': rv_hist, 'iv': vix_hist}).dropna()
        if len(combined) < 60:
            return None

        combined['vrp'] = combined['iv'] - combined['rv']
        recent_vrp = combined['vrp'].iloc[-60:]

        current_vrp = current_iv - forecast_rv
        mean_vrp = recent_vrp.mean()
        std_vrp = recent_vrp.std()

        if std_vrp == 0:
            return 0.0
        return (current_vrp - mean_vrp) / std_vrp

    def _get_rv_percentile(self, as_of_date: datetime,
                           forecast_rv: float) -> Optional[float]:
        """Percentile rank of forecast RV in its 60-day history."""
        if self.rv_series is None:
            return None
        mask = self.rv_series.index <= pd.Timestamp(as_of_date)
        available = self.rv_series[mask].dropna()
        if len(available) < 60:
            return None
        recent = available.iloc[-60:]
        return (recent < forecast_rv).sum() / len(recent)

    def _get_iv_percentile(self, as_of_date: datetime) -> Optional[float]:
        """Percentile rank of current IV in its 60-day history."""
        if self.vix_data is None:
            return None
        mask = self.vix_data.index <= pd.Timestamp(as_of_date)
        available = self.vix_data.loc[mask, 'VIX'].dropna()
        if len(available) < 60:
            return None
        recent = available.iloc[-60:]
        current = recent.iloc[-1]
        return (recent < current).sum() / len(recent)

    def _empty_signal(self, date, reason):
        return {
            'date': date,
            'forecast_rv': None,
            'current_iv': None,
            'rv_minus_iv': None,
            'vix_slope': None,
            'rv_momentum': None,
            'should_trade': False,
            'confidence': reason,
            'model_r2': None,
        }

    # ------------------------------------------------------------------
    # BATCH SIGNALS
    # ------------------------------------------------------------------

    def generate_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Generate trade/no-trade signals for every trading day in range.

        Returns DataFrame with columns:
            date, forecast_rv, current_iv, rv_minus_iv, vix_slope,
            rv_momentum, should_trade, confidence, model_r2
        """
        if self.ohlc is None:
            self.fetch_data(end_date)
        if self.rv_series is None:
            self.compute_realized_vol()

        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')

        # Get trading days from OHLC index
        trading_days = self.ohlc.index[
            (self.ohlc.index >= pd.Timestamp(start_dt)) &
            (self.ohlc.index <= pd.Timestamp(end_dt))
        ]

        signals = []
        for day in trading_days:
            sig = self.fit_and_forecast(day)
            signals.append(sig)

        df = pd.DataFrame(signals)
        return df


# ============================================================================
# STANDALONE TEST
# ============================================================================

if __name__ == '__main__':
    print("=" * 70)
    print("HAR-RV VOLATILITY FORECAST MODEL TEST")
    print("=" * 70)

    forecaster = VolForecaster(symbol='SPY', forecast_horizon=3)
    forecaster.fetch_data(end_date='2025-01-01')
    forecaster.compute_realized_vol()

    print("\n--- Generating signals for backtest period ---")
    signals = forecaster.generate_signals('2024-03-01', '2024-12-31')

    total_days = len(signals)
    trade_days = signals['should_trade'].sum()
    strong_days = (signals['confidence'] == 'STRONG').sum()
    moderate_days = (signals['confidence'] == 'MODERATE').sum()

    print(f"\nSignal Summary (Mar-Dec 2024):")
    print(f"  Total trading days: {total_days}")
    print(f"  Trade signals:      {trade_days} ({trade_days/total_days:.1%})")
    print(f"    STRONG:           {strong_days}")
    print(f"    MODERATE:         {moderate_days}")
    print(f"  No-trade days:      {total_days - trade_days} ({(total_days-trade_days)/total_days:.1%})")

    trade_signals = signals[signals['should_trade']]
    if not trade_signals.empty:
        print(f"\n  Avg forecast RV on trade days: {trade_signals['forecast_rv'].mean():.1f}%")
        print(f"  Avg IV on trade days:          {trade_signals['current_iv'].mean():.1f}%")
        print(f"  Avg edge (RV-IV):              {trade_signals['rv_minus_iv'].mean():.1f} vol pts")
        print(f"  Avg model R2:                  {trade_signals['model_r2'].mean():.3f}")

    no_trade = signals[~signals['should_trade']]
    if not no_trade.empty:
        print(f"\n  Avg forecast RV on no-trade days: {no_trade['forecast_rv'].mean():.1f}%")
        print(f"  Avg IV on no-trade days:           {no_trade['current_iv'].mean():.1f}%")
        print(f"  Avg edge (RV-IV) on no-trade:      {no_trade['rv_minus_iv'].mean():.1f} vol pts")

    # Show monthly breakdown
    print(f"\n--- Monthly Signal Breakdown ---")
    signals['month'] = pd.to_datetime(signals['date']).dt.to_period('M')
    monthly = signals.groupby('month').agg(
        days=('should_trade', 'count'),
        trades=('should_trade', 'sum'),
        avg_edge=('rv_minus_iv', 'mean'),
        avg_rv=('forecast_rv', 'mean'),
        avg_iv=('current_iv', 'mean'),
    )
    for month, row in monthly.iterrows():
        print(f"  {month}: {int(row['trades'])}/{int(row['days'])} trade days, "
              f"avg edge {row['avg_edge']:+.1f}, RV {row['avg_rv']:.1f}% vs IV {row['avg_iv']:.1f}%")
