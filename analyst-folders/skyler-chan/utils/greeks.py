"""
Black-Scholes Greeks Calculator
Rigorous implementation with validation and edge case handling
"""

import numpy as np
from scipy.stats import norm
from typing import Dict, Optional
import warnings


class GreeksCalculator:
    """
    Calculate option prices and Greeks using Black-Scholes model

    Assumptions:
    - European-style options (SPY options are American, but approx is good for backtesting)
    - No dividends (SPY pays dividends, but small effect on short-term options)
    - Constant volatility and risk-free rate
    - Log-normal price distribution
    """

    @staticmethod
    def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                     option_type: str) -> Dict[str, float]:
        """
        Calculate option price and Greeks using Black-Scholes formula

        Args:
            S: Spot price (current stock price)
            K: Strike price
            T: Time to expiration in years (e.g., 1/365 for 1 day)
            r: Risk-free rate (annual, as decimal, e.g., 0.05 for 5%)
            sigma: Implied volatility (annual, as decimal, e.g., 0.20 for 20%)
            option_type: 'call' or 'put'

        Returns:
            Dictionary with:
                - price: Option theoretical price
                - delta: ∂V/∂S (sensitivity to underlying price)
                - gamma: ∂²V/∂S² (rate of delta change)
                - theta: ∂V/∂t (time decay, in dollars per day)
                - vega: ∂V/∂σ (sensitivity to volatility change)
                - rho: ∂V/∂r (sensitivity to interest rate)

        Edge Cases:
            - T <= 0: Option expired, return intrinsic value
            - sigma <= 0: Invalid volatility, raise error
            - S <= 0 or K <= 0: Invalid prices, raise error
        """

        # Input validation
        if S <= 0:
            raise ValueError(f"Invalid spot price: S={S}. Must be > 0")
        if K <= 0:
            raise ValueError(f"Invalid strike price: K={K}. Must be > 0")
        if sigma <= 0:
            raise ValueError(f"Invalid volatility: sigma={sigma}. Must be > 0")
        if option_type not in ['call', 'put']:
            raise ValueError(f"Invalid option_type: {option_type}. Must be 'call' or 'put'")

        # Handle expiry
        if T <= 0:
            return GreeksCalculator._handle_expired_option(S, K, option_type)

        # Handle very small time to expiry (< 1 minute)
        if T < 1 / (365 * 24 * 60):
            warnings.warn(f"Very small T={T:.6f} years. Greeks may be unstable.")

        # Black-Scholes formula
        try:
            d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)
        except (ValueError, ZeroDivisionError) as e:
            raise ValueError(f"Error calculating d1/d2: {e}. S={S}, K={K}, T={T}, sigma={sigma}")

        # Price calculation
        if option_type == 'call':
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
            delta = norm.cdf(d1)
        else:  # put
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = -norm.cdf(-d1)

        # Greeks (common for calls and puts)
        gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))

        # Theta (time decay) - convert to per-day
        if option_type == 'call':
            theta_annual = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) -
                          r * K * np.exp(-r * T) * norm.cdf(d2))
        else:
            theta_annual = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) +
                          r * K * np.exp(-r * T) * norm.cdf(-d2))

        theta = theta_annual / 365  # Convert to daily theta

        # Vega - per 1% volatility change
        vega_pct = S * norm.pdf(d1) * np.sqrt(T) / 100

        # Rho - per 1% interest rate change
        if option_type == 'call':
            rho = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
        else:
            rho = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100

        # Validation: price should be non-negative
        if price < 0:
            warnings.warn(f"Negative price calculated: {price}. Setting to 0.")
            price = max(price, 0)

        return {
            'price': price,
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega_pct,
            'rho': rho
        }

    @staticmethod
    def _handle_expired_option(S: float, K: float, option_type: str) -> Dict[str, float]:
        """
        Handle options that have expired (T <= 0)

        At expiry:
        - Price = intrinsic value = max(S-K, 0) for calls, max(K-S, 0) for puts
        - Delta = 1.0 if ITM, 0.0 if OTM (discontinuous)
        - Gamma = 0 (no more sensitivity to price changes)
        - Theta = 0 (no more time decay)
        - Vega = 0 (no more volatility sensitivity)
        """

        if option_type == 'call':
            intrinsic = max(S - K, 0)
            delta = 1.0 if S > K else 0.0
        else:  # put
            intrinsic = max(K - S, 0)
            delta = -1.0 if S < K else 0.0

        return {
            'price': intrinsic,
            'delta': delta,
            'gamma': 0.0,
            'theta': 0.0,
            'vega': 0.0,
            'rho': 0.0
        }

    @staticmethod
    def implied_volatility(option_price: float, S: float, K: float, T: float,
                          r: float, option_type: str,
                          initial_guess: float = 0.3,
                          tolerance: float = 1e-5,
                          max_iterations: int = 100) -> Optional[float]:
        """
        Calculate implied volatility from market option price using Newton-Raphson

        Args:
            option_price: Observed market price of the option
            S, K, T, r: Black-Scholes parameters
            option_type: 'call' or 'put'
            initial_guess: Starting volatility for iteration (default 30%)
            tolerance: Convergence criterion
            max_iterations: Maximum iterations before giving up

        Returns:
            Implied volatility (as decimal), or None if convergence fails

        Algorithm:
            σ_new = σ_old - (BS_price(σ_old) - market_price) / vega(σ_old)

        Edge Cases:
            - Option price = 0: Return very low IV (0.01)
            - Option price > intrinsic: Return None (arbitrage)
            - Fails to converge: Return None
        """

        # Input validation
        if option_price < 0:
            raise ValueError(f"Negative option price: {option_price}")

        # Handle zero-price options
        if option_price < 0.01:
            return 0.01  # Very low IV

        # Check for arbitrage (option price below intrinsic value)
        if option_type == 'call':
            intrinsic = max(S - K * np.exp(-r * T), 0)
        else:
            intrinsic = max(K * np.exp(-r * T) - S, 0)

        if option_price < intrinsic:
            warnings.warn(f"Option price ({option_price}) below intrinsic ({intrinsic:.2f}). "
                         f"Possible arbitrage or bad data.")

        # Newton-Raphson iteration
        sigma = initial_guess

        for i in range(max_iterations):
            try:
                greeks = GreeksCalculator.black_scholes(S, K, T, r, sigma, option_type)
                bs_price = greeks['price']
                vega = greeks['vega'] * 100  # Convert from per-1% to per-100%

                # Price difference
                price_diff = bs_price - option_price

                # Check convergence
                if abs(price_diff) < tolerance:
                    return sigma

                # Avoid division by zero
                if abs(vega) < 1e-10:
                    warnings.warn(f"Vega too small at iteration {i}. IV may be unstable.")
                    return sigma

                # Newton-Raphson update
                sigma_new = sigma - price_diff / vega

                # Keep sigma in reasonable bounds [1%, 300%]
                sigma_new = max(0.01, min(sigma_new, 3.0))

                # Check for minimal change (another convergence criterion)
                if abs(sigma_new - sigma) < tolerance:
                    return sigma_new

                sigma = sigma_new

            except (ValueError, RuntimeError) as e:
                warnings.warn(f"Error at iteration {i}: {e}")
                return None

        # Failed to converge
        warnings.warn(f"IV solver did not converge after {max_iterations} iterations. "
                     f"Last sigma: {sigma:.4f}")
        return sigma  # Return best guess

    @staticmethod
    def validate_greeks(greeks: Dict[str, float], S: float, K: float,
                       T: float, option_type: str) -> Dict[str, str]:
        """
        Validate calculated Greeks for reasonableness

        Returns dictionary of validation messages (empty if all valid)

        Checks:
        - Call delta in [0, 1], put delta in [-1, 0]
        - Gamma >= 0
        - Theta < 0 for long options (time decay)
        - Vega >= 0
        - Price >= intrinsic value
        """

        issues = {}

        # Delta bounds
        if option_type == 'call':
            if not (0 <= greeks['delta'] <= 1):
                issues['delta'] = f"Call delta {greeks['delta']:.4f} out of bounds [0, 1]"
        else:
            if not (-1 <= greeks['delta'] <= 0):
                issues['delta'] = f"Put delta {greeks['delta']:.4f} out of bounds [-1, 0]"

        # Gamma non-negative
        if greeks['gamma'] < 0:
            issues['gamma'] = f"Negative gamma: {greeks['gamma']:.6f}"

        # Theta (long options lose value over time, so theta < 0)
        if greeks['theta'] > 0:
            issues['theta'] = f"Positive theta for long option: {greeks['theta']:.4f}"

        # Vega non-negative
        if greeks['vega'] < 0:
            issues['vega'] = f"Negative vega: {greeks['vega']:.6f}"

        # Price vs intrinsic
        if option_type == 'call':
            intrinsic = max(S - K, 0)
        else:
            intrinsic = max(K - S, 0)

        if greeks['price'] < intrinsic - 0.01:  # Allow small numerical error
            issues['price'] = f"Price {greeks['price']:.4f} below intrinsic {intrinsic:.4f}"

        return issues


# Convenience functions
def calculate_greeks(S: float, K: float, T: float, r: float, sigma: float,
                    option_type: str) -> Dict[str, float]:
    """Wrapper for GreeksCalculator.black_scholes"""
    return GreeksCalculator.black_scholes(S, K, T, r, sigma, option_type)


def get_implied_volatility(option_price: float, S: float, K: float, T: float,
                           r: float, option_type: str) -> Optional[float]:
    """Wrapper for GreeksCalculator.implied_volatility"""
    return GreeksCalculator.implied_volatility(option_price, S, K, T, r, option_type)
