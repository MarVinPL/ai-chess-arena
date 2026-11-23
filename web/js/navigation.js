/* Issue #299: preserve DOM nodes and event listeners while assigning five views. */
(() => {
  'use strict';
  const routes = [
    ['live', 'Na żywo'],
    ['tournaments', 'Turnieje'],
    ['models', 'Modele'],
    ['history', 'Historia'],
    ['settings', 'Ustawienia']
  ];
  const main = document.getElementById('arena-view-root');
  const nav = document.getElementById('arena-nav');
  if (!main || !nav) return;
  const views = Object.fromEntries(routes.map(([name]) => [name, document.getElementById('view-' + name)]));
  if (Object.values(views).some(view => !view)) return;

  // Moving the existing nodes, rather than cloning or refetching them, keeps
  // one instance of every form, control, DOM ID and registered handler.
  const board = document.getElementById('board');
  const layout = board && board.closest('.layout');
  const games = document.getElementById('games');
  const historyPanel = games && games.closest('section.panel');
  const livePanel = document.getElementById('live-panel');
  const league = document.getElementById('pure-ranking')?.closest('section.panel');
  const leagueGrid = league?.querySelector('.league-grid');
  if (!layout || !historyPanel || !livePanel || !leagueGrid || leagueGrid.children.length !== 4) return;
  const [pureRank, engineRank, modelsColumn, tournamentsColumn] = Array.from(leagueGrid.children);
  const modelPermissions = document.getElementById('model-permissions');
  const storageInfo = document.getElementById('storage-info');
  const message = document.getElementById('league-message');
  const hfShortcut = document.getElementById('hf-quarantine-shortcut');

  const panel = (title, content) => {
    const section = document.createElement('section');
    section.className = 'panel history arena-view-section';
    const heading = document.createElement('h2');
    heading.textContent = title;
    section.append(heading, content);
    return section;
  };
  views.live.append(layout, livePanel);
  views.tournaments.append(panel('Turnieje i drabinka', tournamentsColumn));
  const rankContents = document.createElement('div');
  rankContents.className = 'arena-rankings';
  rankContents.append(pureRank, engineRank);
  views.history.append(historyPanel, panel('Rankingi Elo', rankContents));
  views.models.append(panel('Rejestr i obsługa modeli', modelsColumn));
  if (hfShortcut) views.models.prepend(hfShortcut);
  if (modelPermissions) views.settings.append(panel('Uprawnienia operacyjne', modelPermissions));
  if (storageInfo) views.settings.append(panel('Pamięć i magazyn', storageInfo));
  if (message) views.settings.append(panel('Komunikaty administracyjne', message));
  league.remove();

  const links = Array.from(nav.querySelectorAll('[data-view-link]'));
  const routeName = value => routes.some(([name]) => name === value) ? value : null;
  const hashRoute = () => routeName(window.location.hash.replace(/^#view-/, ''));
  function selectView(name, updateHash = false) {
    const active = routeName(name) || 'live';
    Object.entries(views).forEach(([key, section]) => { section.hidden = key !== active; });
    links.forEach(link => {
      const isActive = link.dataset.viewLink === active;
      if (isActive) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    if (updateHash && window.location.hash !== '#view-' + active)
      window.history.pushState(null, '', '#view-' + active);
  }
  function routeAnchor(anchor) {
    const target = anchor?.getAttribute('href');
    if (!target || !target.startsWith('#') || target.length < 2) return false;
    const id = target.slice(1);
    if (id.startsWith('view-')) return false;
    const element = document.getElementById(id);
    const enclosingView = element?.closest('.arena-view');
    if (!enclosingView) return false;
    selectView(enclosingView.dataset.arenaView, false);
    return true;
  }
  nav.addEventListener('click', event => {
    const link = event.target.closest('[data-view-link]');
    if (!link || !nav.contains(link)) return;
    event.preventDefault();
    selectView(link.dataset.viewLink, true);
    document.getElementById('arena-nav')?.focus({preventScroll:true});
  });
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a[href^="#"]');
    if (!anchor || !routeAnchor(anchor)) return;
    // Native hash navigation still scrolls to the requested section.
  });
  window.addEventListener('hashchange', () => {
    const route = hashRoute();
    if (route) selectView(route);
    else if (window.location.hash.startsWith('#')) {
      const id = window.location.hash.slice(1);
      const element = document.getElementById(id);
      const enclosingView = element?.closest('.arena-view');
      if (enclosingView) selectView(enclosingView.dataset.arenaView);
    }
  });
  const initial = hashRoute();
  if (initial) selectView(initial);
  else if (window.location.hash.length > 1) {
    const target = document.getElementById(window.location.hash.slice(1));
    selectView(target?.closest('.arena-view')?.dataset.arenaView || 'live');
  } else selectView('live');
  main.hidden = false;
  document.documentElement.classList.add('arena-views-ready');
})();
