const currencyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
});

export const formatCurrency = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return currencyFormatter.format(0);
  }
  return currencyFormatter.format(value);
};

export const formatPercent = (value: number | null | undefined): string => {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '0.00%';
  }
  return `${value.toFixed(2)}%`;
};

export const formatTimestamp = (timestamp: string | null): string => {
  if (!timestamp) return 'Unknown';

  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'Unknown';

  return parsed.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  });
};
