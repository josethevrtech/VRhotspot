(function restoreProAdapterReadiness() {
  'use strict';

  let queued = false;
  let refreshRequested = false;

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function value(label, field, initial = '--', extraClass = '') {
    const item = make('div', `kv-item${extraClass ? ` ${extraClass}` : ''}`);
    item.append(make('div', 'kv-label', label));
    const output = make('div', 'kv-value', initial);
    output.dataset.readinessField = field;
    item.appendChild(output);
    return item;
  }

  function createCard() {
    const card = make('section', 'card adapter-readiness-card pro-adapter-readiness');
    card.dataset.adapterReadinessCard = '';
    card.dataset.proReadinessOwned = '1';

    const header = make('div', 'card-header');
    header.appendChild(make('h2', '', 'Adapter readiness'));
    const body = make('div', 'card-body');
    const grid = make('div', 'kv-grid adapter-readiness-grid');
    grid.append(
      value('Recommended', 'recommended', 'Loading...'),
      value('Readiness', 'state'),
      value('Score', 'score'),
      value('6 GHz', 'six-ghz'),
      value('Basic Mode', 'basic-recommended', '--', 'kv-span'),
    );

    const reasonsWrap = make('div', 'mt-12');
    reasonsWrap.appendChild(make('div', 'small muted', 'Top Reasons'));
    const reasons = make('div', 'badge-row adapter-readiness-reasons');
    reasons.dataset.readinessField = 'reasons';
    reasonsWrap.appendChild(reasons);

    const explanation = make('div', 'small mt-10 adapter-readiness-explanation');
    explanation.dataset.readinessField = 'explanation';
    const fallback = make('div', 'small mt-8 warning-text');
    fallback.dataset.readinessField = 'fallback';
    fallback.style.display = 'none';
    body.append(grid, reasonsWrap, explanation, fallback);
    card.append(header, body);
    return card;
  }

  function findAdvancedCard() {
    return document.querySelector(
      '#proStepAdapter [data-adapter-readiness-card], '
      + '#proGuidedStaging [data-adapter-readiness-card], '
      + '.advanced-layout [data-adapter-readiness-card]',
    );
  }

  function reconcile() {
    queued = false;
    if (document.body?.dataset.uiMode !== 'advanced') return;
    const step = document.getElementById('proStepAdapter');
    if (!step) return;

    let card = findAdvancedCard();
    let created = false;
    if (!card) {
      card = createCard();
      created = true;
    }
    card.classList.add('pro-adapter-readiness');
    if (card.parentNode !== step) step.appendChild(card);

    if (created && !refreshRequested) {
      refreshRequested = true;
      window.setTimeout(() => {
        document.getElementById('btnReloadAdapters')?.click();
      }, 0);
    }
  }

  function schedule() {
    if (queued) return;
    queued = true;
    window.setTimeout(reconcile, 0);
  }

  function start() {
    const observer = new MutationObserver(schedule);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-ui-mode'],
    });
    schedule();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
