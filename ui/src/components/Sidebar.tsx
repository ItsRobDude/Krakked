export type SidebarStatusItem = {
  label: string;
  value: string;
  tone?: 'ok' | 'warning' | 'danger' | 'muted';
  hint?: string;
};

export type SidebarAction = {
  label: string;
  tone?: 'default' | 'danger';
  disabled?: boolean;
  onClick?: () => void;
};

export type SidebarMenuItem = {
  label: string;
  href?: string;
  active?: boolean;
  onClick?: () => void;
};

export type SidebarProps = {
  systemStatus: SidebarStatusItem[];
  integrity: SidebarStatusItem[];
  actions: SidebarAction[];
  menu: SidebarMenuItem[];
  note?: string;
};

export function Sidebar({ systemStatus, integrity, actions, menu, note }: SidebarProps) {
  const primaryActions = actions.filter((action) => action.tone !== 'danger');
  const dangerActions = actions.filter((action) => action.tone === 'danger');

  const renderAction = (action: SidebarAction) => (
    <button
      key={action.label}
      type="button"
      className={`sidebar__action${action.tone === 'danger' ? ' sidebar__action--danger' : ''}`}
      disabled={action.disabled}
      onClick={action.onClick}
    >
      {action.label}
    </button>
  );

  return (
    <div className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__mark" aria-hidden="true" />
        <div>
          <p className="sidebar__eyebrow">krakked</p>
          <p className="sidebar__title">Control Room</p>
        </div>
      </div>

      <section className="sidebar__section" aria-label="System status">
        <p className="sidebar__section-title">System Status</p>
        <ul className="sidebar__status-list">
          {systemStatus.map((item) => (
            <li key={item.label} className="sidebar__status-item">
              <span className={`sidebar__status-dot sidebar__status-dot--${item.tone ?? 'muted'}`} aria-hidden="true" />
              <div>
                <p className="sidebar__status-label">{item.label}</p>
                <p className="sidebar__status-value">{item.value}</p>
                {item.hint ? <p className="sidebar__status-hint">{item.hint}</p> : null}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="sidebar__section" aria-label="Integrity">
        <p className="sidebar__section-title">Integrity</p>
        <ul className="sidebar__status-list">
          {integrity.map((item) => (
            <li key={item.label} className="sidebar__status-item">
              <span className={`sidebar__status-dot sidebar__status-dot--${item.tone ?? 'muted'}`} aria-hidden="true" />
              <div>
                <p className="sidebar__status-label">{item.label}</p>
                <p className="sidebar__status-value">{item.value}</p>
                {item.hint ? <p className="sidebar__status-hint">{item.hint}</p> : null}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="sidebar__section" aria-label="Actions">
        <p className="sidebar__section-title">Actions</p>
        <div className="sidebar__actions">
          {primaryActions.map(renderAction)}
        </div>
        {dangerActions.length > 0 ? (
          <div className="sidebar__danger-zone" aria-label="Danger actions">
            <p className="sidebar__danger-title">Danger</p>
            <div className="sidebar__actions">
              {dangerActions.map(renderAction)}
            </div>
          </div>
        ) : null}
      </section>

      <nav className="sidebar__section" aria-label="Dashboard sections">
        <p className="sidebar__section-title">Menu</p>
        <ul className="sidebar__menu-list">
          {menu.map((item) => (
            <li key={item.label}>
              {item.onClick ? (
                <button
                  type="button"
                  className={`sidebar__menu-link sidebar__menu-button${item.active ? ' sidebar__menu-link--active' : ''}`}
                  onClick={item.onClick}
                >
                  {item.label}
                </button>
              ) : (
                <a className={`sidebar__menu-link${item.active ? ' sidebar__menu-link--active' : ''}`} href={item.href}>
                  {item.label}
                </a>
              )}
            </li>
          ))}
        </ul>
      </nav>

      {note ? (
        <div className="sidebar__footer">
          <p className="sidebar__footer-note">{note}</p>
        </div>
      ) : null}
    </div>
  );
}

export default Sidebar;
