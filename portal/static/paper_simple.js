/* paper_simple.js — P0 首屏减法（只做展示分组，不碰交易逻辑）。 */
(function () {
  'use strict';
  function init() {
    var card = document.querySelector('#tab-desk > div:first-child');
    if (!card) return;

    // 直接子块里找“进门路线”和“AI 值班经理”，默认折叠
    var collapsibles = [];
    Array.prototype.forEach.call(card.children, function (el) {
      var text = (el.textContent || '').slice(0, 260);
      if (text.indexOf('您的进门路线') !== -1 || text.indexOf('AI 值班经理') !== -1) {
        collapsibles.push(el);
      }
    });
    collapsibles.forEach(function (el) { el.classList.add('p0-collapsible'); });

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'p0-toggle';
    btn.textContent = '展开新手 / 服务说明';
    btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', function () {
      card.classList.toggle('p0-open');
      var open = card.classList.contains('p0-open');
      btn.textContent = open ? '收起新手 / 服务说明' : '展开新手 / 服务说明';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    var firstTitle = card.querySelector('.col-title');
    if (firstTitle && firstTitle.nextSibling) {
      firstTitle.parentNode.insertBefore(btn, firstTitle.nextSibling);
    } else {
      card.insertBefore(btn, card.firstChild);
    }

    var hint = document.createElement('p');
    hint.className = 'p0-hint';
    hint.textContent = '默认只展示今日计划、风控与成交；服务说明和 AI 值班已收起。';
    btn.insertAdjacentElement('afterend', hint);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
