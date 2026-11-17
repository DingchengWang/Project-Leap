/* Navigation is visible without JavaScript. This only adds mobile disclosure. */
(() => {
  const toggle = document.querySelector('.menu-toggle');
  const nav = document.querySelector('#main-nav');
  if (!toggle || !nav) return;
  toggle.hidden = false;
  document.documentElement.classList.add('menu-ready');
  const setOpen = (open) => {
    toggle.setAttribute('aria-expanded', String(open));
    nav.classList.toggle('is-open', open);
  };
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
      setOpen(false);
      toggle.focus();
    }
  });
  nav.addEventListener('click', (event) => {
    if (event.target.closest('a')) setOpen(false);
  });
})();

/* Every image remains a normal file link, and every gallery image is visible
   until these optional controls are ready. */
(() => {
  const chinese = document.documentElement.lang.startsWith('zh');
  const text = chinese ? {
    choose: '选择图片',
    open: '放大查看',
    original: '原始尺寸',
    file: '打开原图文件',
    close: '关闭',
    viewport: '图片查看区域；原始尺寸下可滚动查看细节',
    position: (index, count, label) => `第 ${index + 1} 张，共 ${count} 张，${label}`,
  } : {
    choose: 'Choose an image',
    open: 'Enlarge image',
    original: 'Original size',
    file: 'Open image file',
    close: 'Close',
    viewport: 'Image viewing area; scroll to inspect details at original size',
    position: (index, count, label) => `Image ${index + 1} of ${count}. ${label}`,
  };

  document.querySelectorAll('[data-gallery]').forEach((gallery, galleryIndex) => {
    const figures = [...gallery.children].filter((child) => child.matches('.media-figure'));
    if (figures.length < 2) return;
    const controls = document.createElement('div');
    controls.className = 'media-gallery-controls';
    controls.setAttribute('role', 'group');
    controls.setAttribute('aria-label', text.choose);
    const status = document.createElement('p');
    status.className = 'visually-hidden';
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');
    const buttons = figures.map((figure, index) => {
      const source = figure.querySelector('.media-link img');
      if (!source) return null;
      figure.id ||= `media-gallery-${galleryIndex + 1}-image-${index + 1}`;
      const label = figure.dataset.galleryLabel || source.alt;
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'media-gallery-button';
      button.setAttribute('aria-controls', figure.id);
      button.setAttribute('aria-label', text.position(index, figures.length, label));
      const thumbnail = document.createElement('img');
      thumbnail.src = source.getAttribute('data-thumbnail') || source.getAttribute('src');
      thumbnail.alt = '';
      thumbnail.loading = 'lazy';
      thumbnail.decoding = 'async';
      const caption = document.createElement('span');
      caption.textContent = label;
      button.append(thumbnail, caption);
      controls.append(button);
      return button;
    });
    // An incomplete figure keeps the entire gallery in its readable HTML form.
    if (buttons.some((button) => !button)) return;
    const show = (index, announce = true) => {
      figures.forEach((figure, item) => {
        const active = item === index;
        figure.hidden = !active;
        figure.classList.toggle('is-active', active);
        buttons[item].setAttribute('aria-pressed', String(active));
        buttons[item].tabIndex = active ? 0 : -1;
      });
      if (announce) status.textContent = buttons[index].getAttribute('aria-label');
    };
    buttons.forEach((button, index) => {
      button.addEventListener('click', () => show(index));
      button.addEventListener('keydown', (event) => {
        let next;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % figures.length;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + figures.length - 1) % figures.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = figures.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        show(next);
        buttons[next].focus();
      });
    });
    gallery.append(controls, status);
    gallery.classList.add('is-enhanced');
    show(0, false);
  });

  const links = [...document.querySelectorAll('.media-figure .media-link')];
  if (!links.length || typeof HTMLDialogElement === 'undefined'
      || typeof HTMLDialogElement.prototype.showModal !== 'function') return;
  const dialog = document.createElement('dialog');
  dialog.className = 'media-dialog';
  dialog.setAttribute('aria-labelledby', 'media-dialog-title');
  const toolbar = document.createElement('div');
  toolbar.className = 'media-dialog-toolbar';
  const title = document.createElement('p');
  title.className = 'media-dialog-title';
  title.id = 'media-dialog-title';
  const actions = document.createElement('div');
  actions.className = 'media-dialog-actions';
  const original = document.createElement('button');
  original.type = 'button';
  original.textContent = text.original;
  original.setAttribute('aria-pressed', 'false');
  const file = document.createElement('a');
  file.textContent = `${text.file} ↗`;
  file.target = '_blank';
  file.rel = 'noopener';
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = text.close;
  close.autofocus = true;
  actions.append(original, file, close);
  toolbar.append(title, actions);
  const viewport = document.createElement('div');
  viewport.className = 'media-dialog-viewport';
  viewport.tabIndex = 0;
  viewport.setAttribute('role', 'region');
  viewport.setAttribute('aria-label', text.viewport);
  const image = document.createElement('img');
  image.decoding = 'async';
  viewport.append(image);
  dialog.append(toolbar, viewport);
  document.body.append(dialog);
  let opener = null;

  original.addEventListener('click', () => {
    const natural = dialog.classList.toggle('is-original-size');
    original.setAttribute('aria-pressed', String(natural));
    viewport.scrollTo(0, 0);
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    document.body.classList.remove('media-dialog-open');
    opener?.focus({ preventScroll: true });
  });
  links.forEach((link) => {
    const source = link.querySelector('img');
    if (!source) return;
    link.setAttribute('aria-haspopup', 'dialog');
    link.setAttribute('aria-label', `${text.open}. ${source.alt}`);
    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      opener = link;
      image.src = link.href;
      image.alt = source.alt;
      file.href = link.href;
      title.textContent = link.closest('.media-figure').dataset.galleryLabel || source.alt;
      dialog.classList.remove('is-original-size');
      original.setAttribute('aria-pressed', 'false');
      document.body.classList.add('media-dialog-open');
      dialog.showModal();
      viewport.scrollTo(0, 0);
    });
  });
})();
