export type PositionRow = {
  pair: string;
  side: 'long' | 'short';
  size: string;
  entry: string;
  mark: string;
  pnl: string;
  status: string;
};

export type PositionsTableProps = {
  positions: PositionRow[];
  title?: string;
  hint?: string;
};

export function PositionsTable({ positions, title = "Open Positions", hint = "Live feed ready" }: PositionsTableProps) {
  if (positions.length === 0) return null;

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>{title}</h2>
        <p className="panel__hint">{hint}</p>
      </div>
      <div className="table table--positions" role="table" aria-label={title}>
        <div className="table__head" role="row">
          <span role="columnheader">Pair</span>
          <span role="columnheader">Side</span>
          <span role="columnheader">Size</span>
          <span role="columnheader">Entry</span>
          <span role="columnheader">Mark</span>
          <span role="columnheader">PnL</span>
          <span role="columnheader">Status</span>
        </div>
        <div className="table__body">
          {positions.map((position) => (
            <div key={`${position.pair}-${position.entry}`} className="table__row" role="row">
              <span role="cell">{position.pair}</span>
              <span role="cell" className={`pill pill--${position.side}`}>{position.side}</span>
              <span role="cell">{position.size}</span>
              <span role="cell">{position.entry}</span>
              <span role="cell">{position.mark}</span>
              <span role="cell" className={position.pnl.startsWith('-') ? 'text--danger' : 'text--success'}>
                {position.pnl}
              </span>
              <span role="cell" className="pill pill--neutral">{position.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default PositionsTable;
