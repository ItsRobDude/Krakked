export type WalletRow = {
  asset: string;
  total: string;
  available: string;
  valueUsd: string;
};

export type WalletTableProps = {
  balances: WalletRow[];
};

export function WalletTable({ balances }: WalletTableProps) {
  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Wallet</h2>
        <p className="panel__hint">Streaming balances</p>
      </div>
      <div className="table table--wallet" role="table" aria-label="Wallet balances">
        <div className="table__head" role="row">
          <span role="columnheader">Asset</span>
          <span role="columnheader">Total</span>
          <span role="columnheader">Available</span>
          <span role="columnheader">USD Value</span>
        </div>
        <div className="table__body">
          {balances.map((balance) => (
            <div key={balance.asset} className="table__row" role="row">
              <span role="cell" className="pill pill--neutral">{balance.asset}</span>
              <span role="cell">{balance.total}</span>
              <span role="cell">{balance.available}</span>
              <span role="cell">{balance.valueUsd}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default WalletTable;
