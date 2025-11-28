import type { ReactNode } from 'react';

export type LayoutProps = {
  title: string;
  subtitle?: string;
  sidebar: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
};

export function Layout({ title, subtitle, sidebar, actions, children, footer }: LayoutProps) {
  return (
    <div className="app-shell app-shell--dashboard">
      <div className="background" aria-hidden="true" />
      <div className="layout">
        <aside className="layout__sidebar" aria-label="Primary navigation">
          {sidebar}
        </aside>
        <div className="layout__main">
          <header className="layout__header">
            <div>
              <p className="eyebrow">Kraken Bot</p>
              <h1>{title}</h1>
              {subtitle ? <p className="subtitle">{subtitle}</p> : null}
            </div>
            {actions ? <div className="layout__actions">{actions}</div> : null}
          </header>

          <div className="layout__content">{children}</div>
          {footer ? <footer className="layout__footer">{footer}</footer> : null}
        </div>
      </div>
    </div>
  );
}

export default Layout;
