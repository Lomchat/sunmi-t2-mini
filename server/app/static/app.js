(() => {
  const app = document.querySelector('#app');
  if (!app) return;
  const csrf = app.dataset.csrf;
  const canViewHistory = app.dataset.canViewHistory === 'true';
  const form = document.querySelector('#print-form');
  const sendButton = document.querySelector('#send-button');
  const toast = document.querySelector('#toast');
  const jobsNode = document.querySelector('#jobs');
  const fileInput = form.querySelector('input[type="file"]');
  const dropzone = document.querySelector('#dropzone');
  const fileName = document.querySelector('#file-name');
  const previewEmpty = document.querySelector('#preview-empty');
  const previewContent = document.querySelector('#preview-content');
  const previewMeta = document.querySelector('#preview-meta');
  const advancedFields = document.querySelector('#advanced-fields');
  const simpleFields = document.querySelector('#simple-fields');
  const advancedModeButton = document.querySelector('#advanced-mode-button');
  const simpleModeButton = document.querySelector('#simple-mode-button');
  const textFeedField = document.querySelector('#text-feed-field');
  const richEditor = document.querySelector('#rich-editor');
  const richImageInput = document.querySelector('#editor-image-input');
  const richImageTools = document.querySelector('#editor-image-tools');
  const richImageSize = document.querySelector('#editor-image-size');
  let advancedMode = true;
  let richPreviewCanvas = null;
  let richPreviewTimer = null;
  let richPreviewGeneration = 0;
  let savedEditorRange = null;
  let selectedImageBlock = null;
  let previewTextCanvas = null;
  let previewTextLines = 0;
  let previewImageCanvas = null;
  let imageProcessingPromise = null;
  let previewImageUrl = '';
  let toastTimer;

  const stateNames = {
    queued: 'En attente',
    leased: 'Impression',
    printed: 'Imprimée',
    failed: 'Échec'
  };

  function alignedRow(className, alignment) {
    const row = document.createElement('div');
    row.className = className;
    row.style.justifyContent = alignment === 'right' ? 'flex-end' : alignment === 'center' ? 'center' : 'flex-start';
    return row;
  }

  function qrSeed(value) {
    let seed = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      seed ^= value.charCodeAt(index);
      seed = Math.imul(seed, 16777619);
    }
    return seed >>> 0;
  }

  function finderPixel(x, y, originX, originY) {
    const localX = x - originX;
    const localY = y - originY;
    if (localX < 0 || localY < 0 || localX > 6 || localY > 6) return null;
    return localX === 0 || localX === 6 || localY === 0 || localY === 6
      || (localX >= 2 && localX <= 4 && localY >= 2 && localY <= 4);
  }

  function pseudoQr(value) {
    const size = 29;
    const qr = document.createElement('div');
    qr.className = 'preview-qr';
    qr.setAttribute('aria-label', 'Aperçu du QR code');
    let seed = qrSeed(value);
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const cell = document.createElement('i');
        const finder = finderPixel(x, y, 0, 0)
          ?? finderPixel(x, y, size - 7, 0)
          ?? finderPixel(x, y, 0, size - 7);
        seed ^= seed << 13;
        seed ^= seed >>> 17;
        seed ^= seed << 5;
        const dark = finder === null ? Boolean(seed & 1) : finder;
        if (dark) cell.className = 'dark';
        qr.append(cell);
      }
    }
    return qr;
  }

  function wrapCanvasText(context, value, width) {
    const lines = [];
    for (const explicitLine of value.split('\n')) {
      if (!explicitLine) {
        lines.push('');
        continue;
      }
      let current = '';
      for (const character of Array.from(explicitLine)) {
        const candidate = current + character;
        if (current && context.measureText(candidate).width > width) {
          lines.push(current);
          current = character;
        } else {
          current = candidate;
        }
      }
      lines.push(current);
    }
    return lines;
  }

  function renderTextBitmap(value, alignment, size, bold) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d', { alpha: false });
    const font = `${bold ? '700' : '400'} ${size}px Arial, Helvetica, sans-serif`;
    context.font = font;
    const lines = wrapCanvasText(context, value, 576);
    const lineHeight = Math.ceil(size * 1.24);
    const fullHeight = Math.max(lineHeight, lines.length * lineHeight);
    form.elements.text.setCustomValidity(fullHeight > 16000 ? 'Le texte est trop long pour un seul ticket.' : '');
    canvas.width = 576;
    canvas.height = Math.min(fullHeight, 16000);
    const drawing = canvas.getContext('2d', { alpha: false });
    drawing.fillStyle = '#ffffff';
    drawing.fillRect(0, 0, canvas.width, canvas.height);
    drawing.fillStyle = '#000000';
    drawing.font = font;
    drawing.textBaseline = 'top';
    drawing.textAlign = alignment;
    const x = alignment === 'right' ? 576 : alignment === 'center' ? 288 : 0;
    lines.forEach((line, index) => drawing.fillText(line, x, index * lineHeight));
    canvas.className = 'preview-text-canvas';
    canvas.setAttribute('aria-label', 'Texte rasterisé tel qu’il sera imprimé');
    previewTextLines = lines.length;
    return canvas;
  }

  function canvasBlob(canvas) {
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('Impossible de générer l’aperçu')), 'image/png');
    });
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('Impossible de lire cette image'));
      image.src = url;
    });
  }

  function histogramLimit(histogram, total, ratio) {
    const target = total * ratio;
    let count = 0;
    for (let value = 0; value < histogram.length; value += 1) {
      count += histogram[value];
      if (count >= target) return value;
    }
    return 255;
  }

  function thermalizeCanvas(canvas) {
    const width = canvas.width;
    const height = canvas.height;
    const context = canvas.getContext('2d', { alpha: false });
    const pixels = context.getImageData(0, 0, width, height);
    const luminance = new Float32Array(width * height);
    const histogram = new Uint32Array(256);
    for (let index = 0; index < luminance.length; index += 1) {
      const offset = index * 4;
      const value = Math.round(
        pixels.data[offset] * 0.2126
        + pixels.data[offset + 1] * 0.7152
        + pixels.data[offset + 2] * 0.0722
      );
      luminance[index] = value;
      histogram[value] += 1;
    }

    let low = histogramLimit(histogram, luminance.length, 0.01);
    let high = histogramLimit(histogram, luminance.length, 0.99);
    if (high - low < 24) {
      low = 0;
      high = 255;
    }
    for (let index = 0; index < luminance.length; index += 1) {
      const normalized = Math.max(0, Math.min(1, (luminance[index] - low) / (high - low)));
      luminance[index] = 255 * Math.pow(normalized, 0.72);
    }

    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const index = y * width + x;
        const printed = luminance[index] < 112 ? 0 : 255;
        const error = luminance[index] - printed;
        const offset = index * 4;
        pixels.data[offset] = printed;
        pixels.data[offset + 1] = printed;
        pixels.data[offset + 2] = printed;
        pixels.data[offset + 3] = 255;
        if (x + 1 < width) luminance[index + 1] += error * 7 / 16;
        if (y + 1 < height) {
          if (x > 0) luminance[index + width - 1] += error * 3 / 16;
          luminance[index + width] += error * 5 / 16;
          if (x + 1 < width) luminance[index + width + 1] += error / 16;
        }
      }
    }
    context.putImageData(pixels, 0, 0);
    return canvas;
  }

  function renderThermalBitmap(image, maxHeight = 4000, forcePrinterWidth = false) {
    const widthScale = 576 / image.naturalWidth;
    if (forcePrinterWidth && image.naturalHeight * widthScale > maxHeight) {
      throw new Error('La composition est trop longue pour un seul ticket.');
    }
    const scale = forcePrinterWidth
      ? widthScale
      : Math.min(widthScale, maxHeight / image.naturalHeight);
    const width = Math.max(1, Math.round(image.naturalWidth * scale));
    const height = Math.max(1, Math.round(image.naturalHeight * scale));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d', { alpha: false });
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, width, height);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.drawImage(image, 0, 0, width, height);
    thermalizeCanvas(canvas);
    canvas.className = 'preview-image preview-image-canvas';
    canvas.setAttribute('aria-label', 'Image thermique telle qu’elle sera imprimée');
    return canvas;
  }

  function richEditorHasContent() {
    return Boolean(richEditor.textContent.trim() || richEditor.querySelector('img'));
  }

  async function captureRichDocument() {
    if (!richEditorHasContent()) return null;
    const editorRect = richEditor.getBoundingClientRect();
    const scale = 576 / richEditor.clientWidth;
    const textRuns = [];
    let contentBottom = 24;
    const walker = document.createTreeWalker(richEditor, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.data || node.parentElement.closest('.editor-image-block')) continue;
      const style = getComputedStyle(node.parentElement);
      let currentRun = null;
      let offset = 0;
      for (const character of Array.from(node.data)) {
        const nextOffset = offset + character.length;
        if (character === '\n') {
          currentRun = null;
          offset = nextOffset;
          continue;
        }
        const range = document.createRange();
        range.setStart(node, offset);
        range.setEnd(node, nextOffset);
        const rect = range.getBoundingClientRect();
        offset = nextOffset;
        if (!rect.width && !character.trim()) continue;
        const top = rect.top - editorRect.top + richEditor.scrollTop;
        const left = rect.left - editorRect.left + richEditor.scrollLeft;
        contentBottom = Math.max(contentBottom, rect.bottom - editorRect.top + richEditor.scrollTop);
        if (currentRun && Math.abs(currentRun.top - top) < 0.75) {
          currentRun.text += character;
        } else {
          currentRun = { text: character, left, top, height: rect.height, style };
          textRuns.push(currentRun);
        }
      }
    }
    richEditor.querySelectorAll('div, p, br, img').forEach((element) => {
      const rect = element.getBoundingClientRect();
      contentBottom = Math.max(contentBottom, rect.bottom - editorRect.top + richEditor.scrollTop);
    });
    const canvasHeight = Math.max(1, Math.ceil((contentBottom + 18) * scale));
    if (canvasHeight > 16000) throw new Error('La composition est trop longue pour un seul ticket.');
    const canvas = document.createElement('canvas');
    canvas.width = 576;
    canvas.height = canvasHeight;
    const context = canvas.getContext('2d', { alpha: false });
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = '#000000';
    context.textBaseline = 'top';
    for (const run of textRuns) {
      const fontSize = parseFloat(run.style.fontSize) * scale;
      context.font = `${run.style.fontStyle} ${run.style.fontWeight} ${fontSize}px ${run.style.fontFamily}`;
      const x = run.left * scale;
      const y = run.top * scale;
      context.fillText(run.text, x, y);
      if (run.style.textDecorationLine.includes('underline')) {
        const width = context.measureText(run.text).width;
        const underlineY = y + run.height * scale - Math.max(1, scale);
        context.fillRect(x, underlineY, width, Math.max(1, scale));
      }
    }
    for (const image of richEditor.querySelectorAll('.editor-image-block img')) {
      const rect = image.getBoundingClientRect();
      context.drawImage(
        image,
        (rect.left - editorRect.left + richEditor.scrollLeft) * scale,
        (rect.top - editorRect.top + richEditor.scrollTop) * scale,
        rect.width * scale,
        rect.height * scale
      );
    }
    thermalizeCanvas(canvas);
    canvas.className = 'preview-text-canvas preview-document-canvas';
    canvas.setAttribute('aria-label', 'Composition exacte telle qu’elle sera imprimée');
    return canvas;
  }

  function appendPreviewFinishing() {
    const qrValue = form.elements.qr.value.trim();
    const feed = Math.max(0, Math.min(12, Number(form.elements.feed.value) || 0));
    if (qrValue) {
      const row = alignedRow('preview-qr-row', 'center');
      row.append(pseudoQr(qrValue));
      previewContent.append(row);
    }
    if (feed) {
      const spacer = document.createElement('div');
      spacer.className = 'preview-feed';
      spacer.style.height = `${feed * 18}px`;
      previewContent.append(spacer);
    }
    if (form.elements.cut.checked) {
      const cut = document.createElement('div');
      cut.className = 'preview-cut';
      previewContent.append(cut);
    }
  }

  async function renderRichPreview() {
    const generation = ++richPreviewGeneration;
    previewMeta.textContent = 'Génération de l’aperçu exact…';
    const canvas = await captureRichDocument();
    if (generation !== richPreviewGeneration) return richPreviewCanvas;
    richPreviewCanvas = canvas;
    previewContent.replaceChildren();
    const hasQr = Boolean(form.elements.qr.value.trim());
    const hasContent = Boolean(canvas || hasQr);
    previewEmpty.classList.toggle('hidden', hasContent);
    previewContent.classList.toggle('hidden', !hasContent);
    if (canvas) previewContent.append(canvas);
    if (hasContent) appendPreviewFinishing();
    previewMeta.textContent = canvas
      ? `Composition exacte · ${canvas.width} × ${canvas.height} px`
      : hasQr ? 'QR code simulé' : 'Largeur d’impression : 576 pixels';
    return canvas;
  }

  function scheduleRichPreview() {
    clearTimeout(richPreviewTimer);
    richPreviewTimer = setTimeout(() => {
      renderRichPreview().catch((error) => {
        console.error(error);
        previewMeta.textContent = 'Impossible de générer l’aperçu.';
      });
    }, 140);
  }

  function setComposerMode(useAdvancedMode) {
    advancedMode = useAdvancedMode;
    advancedFields.classList.toggle('hidden', !advancedMode);
    simpleFields.classList.toggle('hidden', advancedMode);
    textFeedField.classList.toggle('hidden', advancedMode);
    advancedModeButton.classList.toggle('active', advancedMode);
    simpleModeButton.classList.toggle('active', !advancedMode);
    if (advancedMode) scheduleRichPreview();
    else updatePreview();
  }

  function rememberEditorSelection() {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (richEditor.contains(range.commonAncestorContainer)) savedEditorRange = range.cloneRange();
  }

  function restoreEditorSelection() {
    richEditor.focus();
    if (!savedEditorRange) return;
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(savedEditorRange);
  }

  function normalizeEditorAlignment() {
    richEditor.querySelectorAll('[style]').forEach((element) => {
      const styleText = element.getAttribute('style') || '';
      const match = styleText.match(/(?:^|;)\s*text-align\s*:\s*(left|center|right)\s*;?/i);
      if (!match) return;
      const alignment = match[1].toLowerCase();
      element.classList.remove('editor-align-left', 'editor-align-center', 'editor-align-right');
      element.classList.add(`editor-align-${alignment}`);
      const remainingStyles = styleText.replace(match[0], '').trim().replace(/^;+|;+$/g, '');
      if (remainingStyles) element.setAttribute('style', remainingStyles);
      else element.removeAttribute('style');
    });
  }

  function selectEditorImage(block) {
    if (selectedImageBlock) selectedImageBlock.classList.remove('selected');
    selectedImageBlock = block;
    richImageTools.classList.toggle('hidden', !block);
    if (block) {
      block.classList.add('selected');
      richImageSize.value = parseInt(block.querySelector('img').style.width, 10) || 100;
    }
  }

  function readFileDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Impossible de lire cette image'));
      reader.readAsDataURL(file);
    });
  }

  async function insertRichImage(file) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) throw new Error('Image trop volumineuse (5 Mio maximum)');
    const source = await readFileDataUrl(file);
    await loadImage(source);
    const block = document.createElement('div');
    block.className = 'editor-image-block';
    block.contentEditable = 'false';
    block.style.textAlign = 'center';
    const image = document.createElement('img');
    image.src = source;
    image.alt = '';
    image.style.width = '100%';
    block.append(image);
    restoreEditorSelection();
    const selection = window.getSelection();
    if (selection && selection.rangeCount && richEditor.contains(selection.getRangeAt(0).commonAncestorContainer)) {
      const range = selection.getRangeAt(0);
      range.collapse(false);
      range.insertNode(block);
    } else {
      richEditor.append(block);
    }
    const followingLine = document.createElement('div');
    followingLine.append(document.createElement('br'));
    block.after(followingLine);
    selectEditorImage(block);
    richImageInput.value = '';
    scheduleRichPreview();
  }

  function updatePreview() {
    if (advancedMode) {
      scheduleRichPreview();
      return;
    }
    const text = form.elements.text.value.trim();
    const qrValue = form.elements.qr.value.trim();
    const alignment = ['left', 'center', 'right'].includes(form.elements.align.value)
      ? form.elements.align.value : 'left';
    const size = Math.max(16, Math.min(64, Number(form.elements.size.value) || 28));
    const textFeed = Math.max(0, Math.min(12, Number(form.elements.text_feed.value) || 0));
    const feed = Math.max(0, Math.min(12, Number(form.elements.feed.value) || 0));
    const imageSelected = Boolean(fileInput.files[0]);
    const hasImage = Boolean(imageSelected && previewImageCanvas);
    const hasPrintableContent = Boolean(text || qrValue || imageSelected);

    previewContent.replaceChildren();
    previewTextCanvas = null;
    previewTextLines = 0;
    form.elements.text.setCustomValidity('');
    previewEmpty.classList.toggle('hidden', hasPrintableContent);
    previewContent.classList.toggle('hidden', !hasPrintableContent);
    if (!hasPrintableContent) {
      previewMeta.textContent = 'Largeur simulée : 576 points d’impression';
      return;
    }

    if (text) {
      previewTextCanvas = renderTextBitmap(text, alignment, size, form.elements.bold.checked);
      previewContent.append(previewTextCanvas);
      if (textFeed) {
        const spacer = document.createElement('div');
        spacer.className = 'preview-feed preview-text-feed';
        spacer.style.height = `${textFeed * Math.max(14, size * 0.54 * 1.24)}px`;
        previewContent.append(spacer);
      }
    }

    if (hasImage) {
      const row = alignedRow('preview-image-row', alignment);
      row.append(previewImageCanvas);
      previewContent.append(row);
    } else if (imageSelected) {
      const pending = document.createElement('div');
      pending.className = 'preview-processing';
      pending.textContent = 'Optimisation de l’image pour le papier thermique…';
      previewContent.append(pending);
    }

    if (qrValue) {
      const row = alignedRow('preview-qr-row', alignment);
      row.append(pseudoQr(qrValue));
      previewContent.append(row);
    }

    if (feed) {
      const spacer = document.createElement('div');
      spacer.className = 'preview-feed';
      spacer.style.height = `${feed * Math.max(14, size * 0.54 * 1.24)}px`;
      previewContent.append(spacer);
    }

    if (form.elements.cut.checked) {
      const cut = document.createElement('div');
      cut.className = 'preview-cut';
      previewContent.append(cut);
    }

    requestAnimationFrame(() => {
      const details = [];
      if (previewTextCanvas) {
        details.push(`${previewTextLines} ligne${previewTextLines > 1 ? 's' : ''} exacte${previewTextLines > 1 ? 's' : ''}`);
        details.push(`${text.length}/8 000 caractères`);
      }
      if (hasImage) details.push(`${previewImageCanvas.width} × ${previewImageCanvas.height} px exacts`);
      else if (imageSelected) details.push('traitement de l’image en cours');
      if (qrValue) details.push('QR code simulé');
      previewMeta.textContent = details.join(' · ') || 'Largeur simulée : 576 points d’impression';
    });
  }

  function showToast(message, isError = false) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.className = `toast show${isError ? ' error' : ''}`;
    toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3800);
  }

  function formatDate(timestamp) {
    if (!timestamp) return 'Jamais';
    return new Intl.DateTimeFormat('fr-FR', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).format(new Date(timestamp * 1000));
  }

  async function api(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    if (response.status === 401) {
      location.href = '/login';
      throw new Error('Session expirée');
    }
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new Error(data?.detail || `Erreur HTTP ${response.status}`);
    return data;
  }

  async function refreshStatus() {
    try {
      const data = await api('/api/admin/status');
      const pill = document.querySelector('#online-pill');
      const orb = document.querySelector('#device-orb');
      const detail = document.querySelector('#device-detail');
      const error = document.querySelector('#device-error');
      pill.textContent = data.online ? 'En ligne' : 'Hors ligne';
      pill.className = `status-pill ${data.online ? 'online' : 'offline'}`;
      orb.className = `device-orb ${data.online ? 'online' : 'offline'}`;
      detail.textContent = data.last_seen
        ? `${data.device_model || 'SUNMI'} · application ${data.app_version || 'inconnue'} · imprimante ${data.printer_status || 'inconnue'} · vu ${formatDate(data.last_seen)}`
        : 'En attente de la première connexion de l’application.';
      if (data.last_error) {
        error.textContent = data.last_error;
        error.classList.remove('hidden');
      } else {
        error.classList.add('hidden');
      }
      document.querySelector('#count-queued').textContent = data.queue.queued + data.queue.leased;
      const printedCount = document.querySelector('#count-printed');
      const failedCount = document.querySelector('#count-failed');
      if (printedCount) printedCount.textContent = data.queue.printed;
      if (failedCount) failedCount.textContent = data.queue.failed;
    } catch (error) {
      console.error(error);
    }
  }

  function renderJobs(jobs) {
    jobsNode.replaceChildren();
    if (!jobs.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = 'Aucune impression pour le moment.';
      jobsNode.append(empty);
      return;
    }
    for (const job of jobs) {
      const row = document.createElement('article');
      row.className = 'job';
      row.dataset.state = job.state;

      const dot = document.createElement('span');
      dot.className = 'job-dot';
      const copy = document.createElement('div');
      const title = document.createElement('div');
      title.className = 'job-title';
      title.textContent = job.summary;
      const meta = document.createElement('div');
      meta.className = 'job-meta';
      meta.textContent = `${formatDate(job.created_at)} · tentative ${job.attempts}`;
      copy.append(title, meta);
      if (job.error) {
        const error = document.createElement('div');
        error.className = 'job-error';
        error.textContent = job.error;
        copy.append(error);
      }
      let action;
      if (job.state === 'failed') {
        action = document.createElement('button');
        action.className = 'retry';
        action.type = 'button';
        action.textContent = 'Relancer';
        action.addEventListener('click', () => retryJob(job.id));
      } else {
        action = document.createElement('span');
        action.className = 'state-label';
        action.textContent = stateNames[job.state] || job.state;
      }
      row.append(dot, copy, action);
      jobsNode.append(row);
    }
  }

  async function refreshJobs() {
    try {
      const data = await api('/api/admin/jobs');
      renderJobs(data.jobs);
    } catch (error) {
      console.error(error);
    }
  }

  async function retryJob(id) {
    try {
      await api(`/api/admin/jobs/${encodeURIComponent(id)}/retry`, {
        method: 'POST', headers: { 'X-CSRF-Token': csrf }
      });
      showToast('Impression remise en file.');
      const refreshes = [refreshStatus()];
      if (canViewHistory) refreshes.push(refreshJobs());
      await Promise.all(refreshes);
    } catch (error) {
      showToast(error.message, true);
    }
  }

  advancedModeButton.addEventListener('click', () => setComposerMode(true));
  simpleModeButton.addEventListener('click', () => setComposerMode(false));

  document.addEventListener('selectionchange', rememberEditorSelection);
  richEditor.addEventListener('paste', (event) => {
    event.preventDefault();
    document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
  });
  richEditor.addEventListener('click', (event) => {
    const block = event.target.closest('.editor-image-block');
    selectEditorImage(block || null);
  });
  document.querySelectorAll('[data-editor-command]').forEach((button) => {
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', () => {
      restoreEditorSelection();
      document.execCommand(button.dataset.editorCommand, false, null);
      if (button.dataset.editorCommand.startsWith('justify')) normalizeEditorAlignment();
      rememberEditorSelection();
      scheduleRichPreview();
    });
  });
  document.querySelector('#editor-font-size').addEventListener('change', (event) => {
    restoreEditorSelection();
    document.execCommand('fontSize', false, event.target.value);
    rememberEditorSelection();
    scheduleRichPreview();
  });
  document.querySelector('#editor-insert-image').addEventListener('click', () => richImageInput.click());
  richImageInput.addEventListener('change', () => {
    insertRichImage(richImageInput.files[0]).catch((error) => showToast(error.message, true));
  });
  richImageSize.addEventListener('input', () => {
    if (!selectedImageBlock) return;
    selectedImageBlock.querySelector('img').style.width = `${richImageSize.value}%`;
    scheduleRichPreview();
  });
  document.querySelectorAll('[data-image-align]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!selectedImageBlock) return;
      selectedImageBlock.style.textAlign = button.dataset.imageAlign;
      scheduleRichPreview();
    });
  });
  document.querySelector('#editor-image-up').addEventListener('click', () => {
    if (!selectedImageBlock || !selectedImageBlock.previousElementSibling) return;
    selectedImageBlock.previousElementSibling.before(selectedImageBlock);
    scheduleRichPreview();
  });
  document.querySelector('#editor-image-down').addEventListener('click', () => {
    if (!selectedImageBlock || !selectedImageBlock.nextElementSibling) return;
    selectedImageBlock.nextElementSibling.after(selectedImageBlock);
    scheduleRichPreview();
  });
  document.querySelector('#editor-image-remove').addEventListener('click', () => {
    if (!selectedImageBlock) return;
    selectedImageBlock.remove();
    selectEditorImage(null);
    scheduleRichPreview();
  });

  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    if (previewImageUrl) URL.revokeObjectURL(previewImageUrl);
    previewImageUrl = file ? URL.createObjectURL(file) : '';
    previewImageCanvas = null;
    imageProcessingPromise = null;
    fileInput.setCustomValidity('');
    fileName.textContent = file ? file.name : '';
    dropzone.classList.toggle('has-file', Boolean(file));
    updatePreview();
    if (file) {
      const selectedUrl = previewImageUrl;
      imageProcessingPromise = loadImage(selectedUrl)
        .then((image) => {
          if (previewImageUrl !== selectedUrl) return;
          previewImageCanvas = renderThermalBitmap(image);
          updatePreview();
        })
        .catch((error) => {
          if (previewImageUrl !== selectedUrl) return;
          fileInput.setCustomValidity(error.message);
          showToast(error.message, true);
          updatePreview();
        });
    }
  });

  form.addEventListener('input', updatePreview);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    sendButton.disabled = true;
    sendButton.querySelector('span').textContent = 'Envoi en cours…';
    try {
      let body;
      if (advancedMode) {
        clearTimeout(richPreviewTimer);
        const documentCanvas = await renderRichPreview();
        body = new FormData(form);
        body.delete('image');
        body.delete('text_bitmap');
        body.delete('text_feed');
        const summary = richEditor.textContent.trim().replace(/\s+/g, ' ').slice(0, 8000);
        body.set('text', summary || (documentCanvas ? 'Composition avec image' : ''));
        if (documentCanvas) {
          body.append('document_bitmap', await canvasBlob(documentCanvas), 'composition-ticket.png');
        }
      } else {
        if (imageProcessingPromise) await imageProcessingPromise;
        updatePreview();
        body = new FormData(form);
        if (previewTextCanvas && form.elements.text.value.trim()) {
          body.append('text_bitmap', await canvasBlob(previewTextCanvas), 'texte-rasterise.png');
        }
        if (fileInput.files[0]) {
          if (!previewImageCanvas) throw new Error('L’image n’a pas pu être préparée');
          const originalName = fileInput.files[0].name.replace(/\.[^.]+$/, '') || 'image';
          body.delete('image');
          body.append('image', await canvasBlob(previewImageCanvas), `${originalName}-thermique.png`);
        }
      }
      await api('/api/admin/jobs', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
        body
      });
      form.reset();
      if (advancedMode) {
        richEditor.replaceChildren();
        selectEditorImage(null);
        richPreviewCanvas = null;
      }
      if (previewImageUrl) URL.revokeObjectURL(previewImageUrl);
      previewImageUrl = '';
      previewImageCanvas = null;
      imageProcessingPromise = null;
      fileName.textContent = '';
      dropzone.classList.remove('has-file');
      updatePreview();
      showToast('Impression ajoutée à la file.');
      const refreshes = [refreshStatus()];
      if (canViewHistory) refreshes.push(refreshJobs());
      await Promise.all(refreshes);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      sendButton.disabled = false;
      sendButton.querySelector('span').textContent = 'Envoyer à l’imprimante';
    }
  });

  const refreshButton = document.querySelector('#refresh-button');
  if (refreshButton) {
    refreshButton.addEventListener('click', () => {
      refreshJobs();
      refreshStatus();
    });
  }

  refreshStatus();
  setComposerMode(true);
  if (canViewHistory) refreshJobs();
  setInterval(refreshStatus, 5000);
  if (canViewHistory) setInterval(refreshJobs, 5000);
})();
