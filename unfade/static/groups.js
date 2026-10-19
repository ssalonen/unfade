(() => {
  const section = document.querySelector('[data-group-board]');
  if (!section) return;
  const t = (text, values = {}) => ((window.unfadeText || {})[text] || text).replace(/\{(\w+)\}/g, (_, key) => values[key]);
  const board = document.getElementById('group-board');
  const addCard = document.getElementById('add-group-card');
  const target = document.getElementById('move-target');
  const moveButton = document.getElementById('move-selected');
  const undoButton = document.getElementById('undo-group');
  const redoButton = document.getElementById('redo-group');
  const saveForm = document.getElementById('grouping-form');
  const saveButton = document.getElementById('save-grouping');
  const status = document.getElementById('group-status');
  const modal = document.getElementById('image-modal');
  const deleteModal = document.getElementById('delete-modal');
  const undoStack = [];
  const redoStack = [];
  let newGroupNumber = 1;
  let draggedCards = [];
  let dragGhost = null;
  let pendingDelete = null;
  let activeCard = null;
  let activeImageKind = 'original';
  let saving = false;
  const groups = () => [...board.querySelectorAll('.color-group')];
  const cards = () => [...board.querySelectorAll('.image-card')];
  const body = group => group.querySelector('.group-body');
  const marker = group => group.querySelector('.group-empty');
  // The shown (translated) name; data-group-label keeps the stored one for saving.
  const name = group => group.querySelector('h3').textContent;
  const groupForKey = key => groups().find(group => group.dataset.groupKey === key);
  const countLabel = count => count === 1 ? t('1 image') : t('{count} images', {count});
  // unfade keys cards by a sortable index and shows the file name instead.
  const title = card => card.dataset.label || card.dataset.itemId;

  function snapshot() {
    return {
      groups: groups().map(group => ({key: group.dataset.groupKey, label: name(group)})),
      assignments: Object.fromEntries(cards().sort((a, b) => a.dataset.itemId.localeCompare(b.dataset.itemId))
        .map(card => [card.dataset.itemId, card.closest('.color-group').dataset.groupKey])),
    };
  }
  const initialState = JSON.stringify(snapshot());
  const initialAssignments = snapshot().assignments;
  const initialMembers = Object.fromEntries(groups().map(group => [group.dataset.groupKey,
    [...body(group).querySelectorAll('.image-card')].map(card => card.dataset.itemId).sort().join('|')]));
  document.querySelectorAll('[data-requires-saved-grouping] button').forEach(button => {
    button.dataset.initiallyDisabled = button.disabled ? '1' : '0';
  });

  function refresh() {
    const currentGroups = groups();
    currentGroups.forEach(group => {
      const members = [...body(group).querySelectorAll('.image-card')];
      group.querySelector('.group-count').textContent = countLabel(members.length);
      group.querySelector('.delete-group').disabled = currentGroups.length === 1;
      const changedMembers = members.map(card => card.dataset.itemId).sort().join('|') !== initialMembers[group.dataset.groupKey];
      if (changedMembers && group.dataset.approved === '1') group.querySelector('.group-approval').textContent = t('approval is withdrawn on saving');
      else group.querySelector('.group-approval').textContent = group.dataset.approved === '1' ? `✓ ${t('approved')}` : t('awaiting approval');
    });
    const selected = cards().filter(card => card.querySelector('.image-pick').checked).length;
    document.getElementById('selection-count').textContent = selected === 1 ? t('1 image selected') : t('{count} images selected', {count: selected});
    moveButton.disabled = selected === 0;
    undoButton.disabled = undoStack.length === 0;
    redoButton.disabled = redoStack.length === 0;
    if (!groupForKey(target.value)) target.value = currentGroups[0].dataset.groupKey;
    cards().forEach(card => card.classList.toggle('selected', card.querySelector('.image-pick').checked));
    const dirty = JSON.stringify(snapshot()) !== initialState;
    saveButton.disabled = !dirty || saving;
    document.querySelectorAll('[data-requires-saved-grouping] button').forEach(button => {
      button.disabled = dirty || button.dataset.initiallyDisabled === '1';
    });
    section.classList.toggle('has-unsaved-groups', dirty);
    if (dirty && !status.dataset.error) status.textContent = t('Unsaved group changes. Undo and Redo work until you save.');
    if (!dirty && !status.dataset.error) status.textContent = '';
  }
  function execute(command) {
    command.redo(); undoStack.push(command); redoStack.length = 0;
    delete status.dataset.error;
    refresh(); status.textContent = command.message;
  }
  function undo() {
    const command = undoStack.pop(); if (!command) return;
    command.undo(); redoStack.push(command);
    delete status.dataset.error;
    refresh(); status.textContent = `Undo: ${command.message}`;
  }
  function redo() {
    const command = redoStack.pop(); if (!command) return;
    command.redo(); undoStack.push(command);
    delete status.dataset.error;
    refresh(); status.textContent = `Redo: ${command.message}`;
  }
  function move(toMove, destination) {
    const moved = toMove.filter(card => card.closest('.color-group') !== destination);
    if (!moved.length) return;
    const origins = moved.map(card => ({card, parent: card.parentElement, next: card.nextSibling,
      selected: card.querySelector('.image-pick').checked}));
    execute({
      message: t('{images} moved to group {target}.', {images: countLabel(moved.length), target: name(destination)}),
      redo() { moved.forEach(card => { body(destination).insertBefore(card, marker(destination)); card.querySelector('.image-pick').checked = false; }); },
      undo() { [...origins].reverse().forEach(({card, parent, next, selected}) => {
        parent.insertBefore(card, next && next.parentElement === parent ? next : parent.querySelector('.group-empty'));
        card.querySelector('.image-pick').checked = selected;
      }); },
    });
  }
  function createGroup() {
    const input = document.getElementById('new-group-name');
    const label = input.value.trim();
    if (!label) { input.focus(); return; }
    const key = `new-${newGroupNumber++}`;
    const group = document.createElement('section');
    group.className = 'color-group'; group.dataset.groupKey = key; group.dataset.groupLabel = label; group.dataset.approved = '0';
    group.innerHTML = '<header class="color-group-head"><span class="eyebrow"></span><h3></h3><p><span class="group-count"></span> · <span class="group-approval"></span></p><button type="button" class="delete-group"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v6M14 11v6"/></svg></button></header><div class="group-body"><p class="group-empty"></p></div>';
    group.querySelector('.eyebrow').textContent = t('Own group');
    group.querySelector('.group-count').textContent = countLabel(0);
    group.querySelector('.group-approval').textContent = t('awaiting approval');
    group.querySelector('.delete-group').title = t('Delete group');
    group.querySelector('.group-empty').textContent = t('Drop images into this group');
    group.querySelector('h3').textContent = label;
    group.querySelector('.delete-group').setAttribute('aria-label', t('Delete group {name}', {name: label}));
    const option = document.createElement('option'); option.value = key; option.textContent = label;
    execute({message: t('Group {name} added.', {name: label}),
      redo() { board.insertBefore(group, addCard); target.appendChild(option); target.value = key; },
      undo() { group.remove(); option.remove(); },
    });
    input.value = '';
  }
  function deleteGroup(group, destination) {
    const option = [...target.options].find(item => item.value === group.dataset.groupKey);
    const groupNext = group.nextSibling, optionNext = option.nextSibling;
    const contents = [...body(group).querySelectorAll('.image-card')].map(card => ({card, selected: card.querySelector('.image-pick').checked}));
    execute({message: contents.length ? t('Group {name} deleted; {images} moved to group {target}.', {name: name(group), images: countLabel(contents.length), target: name(destination)}) : t('Group {name} deleted.', {name: name(group)}),
      redo() {
        contents.forEach(({card}) => { body(destination).insertBefore(card, marker(destination)); card.querySelector('.image-pick').checked = false; });
        group.remove(); option.remove();
        if (!groupForKey(target.value)) target.value = destination.dataset.groupKey;
      },
      undo() {
        board.insertBefore(group, groupNext && groupNext.parentElement === board ? groupNext : addCard);
        target.insertBefore(option, optionNext && optionNext.parentElement === target ? optionNext : null);
        contents.forEach(({card, selected}) => { body(group).insertBefore(card, marker(group)); card.querySelector('.image-pick').checked = selected; });
      },
    });
  }
  function openDelete(group) {
    const alternatives = groups().filter(item => item !== group);
    if (!alternatives.length) return;
    const count = body(group).querySelectorAll('.image-card').length;
    if (!count) { deleteGroup(group, alternatives[0]); return; }
    pendingDelete = group;
    document.getElementById('delete-title').textContent = t('Delete {name}?', {name: name(group)});
    document.getElementById('delete-explanation').textContent = t('{images} belong to this group. Choose the group to move them to. Undo brings back the group and its images.', {images: countLabel(count)});
    document.getElementById('delete-target').replaceChildren(...alternatives.map(item => {
      const option = document.createElement('option'); option.value = item.dataset.groupKey; option.textContent = name(item); return option;
    }));
    deleteModal.showModal();
  }
  function openImage(card, kind = 'original') {
    const src = kind === 'preview' ? card.dataset.preview : card.dataset.original;
    if (!src) return;
    activeCard = card; activeImageKind = kind;
    document.getElementById('modal-id').textContent = `${title(card)} · ${kind === 'preview' ? t('corrected preview') : t('original')}`;
    document.getElementById('modal-image').src = src;
    document.getElementById('modal-image').alt = `${title(card)} ${kind === 'preview' ? t('corrected preview') : t('original')}`;
    document.getElementById('modal-group').textContent = t('Group: {name}', {name: name(card.closest('.color-group'))});
    document.getElementById('modal-note').textContent = card.dataset.note || '';
    const order = cards().sort((a, b) => a.dataset.itemId.localeCompare(b.dataset.itemId));
    const index = order.indexOf(card);
    document.getElementById('modal-prev').disabled = index === 0;
    document.getElementById('modal-next').disabled = index === order.length - 1;
    if (!modal.open) modal.showModal();
  }
  function adjacent(direction) {
    const order = cards().sort((a, b) => a.dataset.itemId.localeCompare(b.dataset.itemId));
    const next = order[order.indexOf(activeCard) + direction];
    if (next) openImage(next, activeImageKind);
  }

  board.addEventListener('change', event => { if (event.target.matches('.image-pick')) refresh(); });
  board.addEventListener('click', event => {
    const thumbnail = event.target.closest('.thumbnail-open');
    if (thumbnail) { openImage(thumbnail.closest('.image-card')); return; }
    const deleteButton = event.target.closest('.delete-group');
    if (deleteButton) openDelete(deleteButton.closest('.color-group'));
  });
  board.addEventListener('keydown', event => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.image-card')) {
      event.preventDefault(); openImage(event.target);
    }
  });
  document.querySelectorAll('.large-image-button').forEach(button => button.addEventListener('click', () => {
    const card = cards().find(item => item.dataset.itemId === button.dataset.itemId);
    if (card) openImage(card, button.dataset.imageKind);
  }));
  board.addEventListener('dragstart', event => {
    const card = event.target.closest('.image-card'); if (!card) return;
    const source = card.closest('.color-group');
    draggedCards = card.querySelector('.image-pick').checked
      ? [...body(source).querySelectorAll('.image-card')].filter(item => item.querySelector('.image-pick').checked) : [card];
    event.dataTransfer.setData('text/plain', card.dataset.itemId);
    event.dataTransfer.effectAllowed = 'move';
    draggedCards.forEach(item => item.classList.add('dragging'));
    source.classList.add('drag-source');
    groups().forEach(group => { group.dataset.dragCount = t('Drop here · {images}', {images: countLabel(draggedCards.length)}); });
    dragGhost = document.createElement('div'); dragGhost.className = 'drag-ghost';
    const inner = document.createElement('div'); inner.className = 'drag-ghost-inner';
    const thumb = card.querySelector('.thumbnail-open img');
    if (thumb) inner.appendChild(thumb.cloneNode(true));
    const label = document.createElement('strong'); label.textContent = draggedCards.length === 1 ? title(card) : t('Selected images'); inner.appendChild(label);
    const count = document.createElement('span'); count.textContent = countLabel(draggedCards.length);
    dragGhost.append(inner, count);
    const origin = card.getBoundingClientRect(); dragGhost.style.left = `${Math.max(0, origin.left)}px`; dragGhost.style.top = `${Math.max(0, origin.top)}px`;
    document.body.appendChild(dragGhost); event.dataTransfer.setDragImage(dragGhost, 45, 45);
  });
  board.addEventListener('dragend', () => {
    draggedCards.forEach(card => card.classList.remove('dragging')); draggedCards = [];
    dragGhost?.remove(); dragGhost = null;
    groups().forEach(group => { group.classList.remove('drag-over', 'drag-source'); delete group.dataset.dragCount; });
  });
  board.addEventListener('dragover', event => {
    const group = event.target.closest('.color-group'); if (!group) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move'; group.classList.add('drag-over');
  });
  board.addEventListener('dragleave', event => {
    const group = event.target.closest('.color-group');
    if (group && !group.contains(event.relatedTarget)) group.classList.remove('drag-over');
  });
  board.addEventListener('drop', event => {
    const group = event.target.closest('.color-group'); if (!group) return;
    event.preventDefault(); group.classList.remove('drag-over');
    const card = cards().find(item => item.dataset.itemId === event.dataTransfer.getData('text/plain'));
    if (card) move(draggedCards.length ? draggedCards : [card], group);
  });
  moveButton.addEventListener('click', () => move(cards().filter(card => card.querySelector('.image-pick').checked), groupForKey(target.value)));
  undoButton.addEventListener('click', undo);
  redoButton.addEventListener('click', redo);
  document.getElementById('create-group').addEventListener('click', createGroup);
  document.getElementById('new-group-name').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); createGroup(); }
  });
  document.getElementById('delete-cancel').addEventListener('click', () => { pendingDelete = null; deleteModal.close(); });
  deleteModal.addEventListener('close', () => { pendingDelete = null; });
  document.getElementById('delete-confirm').addEventListener('click', () => {
    if (!pendingDelete) return;
    const destination = groupForKey(document.getElementById('delete-target').value);
    if (destination) deleteGroup(pendingDelete, destination);
    pendingDelete = null; deleteModal.close();
  });
  document.getElementById('modal-close').addEventListener('click', () => modal.close());
  modal.addEventListener('click', event => { if (event.target === modal) modal.close(); });
  document.getElementById('modal-prev').addEventListener('click', () => adjacent(-1));
  document.getElementById('modal-next').addEventListener('click', () => adjacent(1));
  modal.addEventListener('keydown', event => { if (event.key === 'ArrowLeft') adjacent(-1); if (event.key === 'ArrowRight') adjacent(1); });
  document.querySelectorAll('[data-requires-saved-grouping] form').forEach(form => form.addEventListener('submit', event => {
    if (JSON.stringify(snapshot()) !== initialState) {
      event.preventDefault();
      status.textContent = t('Save the group changes before adjusting or exporting.');
      document.getElementById('groups').scrollIntoView({block: 'start'});
    }
  }));
  saveForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (saveButton.disabled) return;
    saveForm.elements.namedItem('state').value = JSON.stringify(snapshot());
    saving = true; refresh(); status.textContent = t('Saving groups…');
    try {
      const response = await fetch(saveForm.action, {method: 'POST', body: new FormData(saveForm)});
      if (!response.ok) {
        const problem = await response.json();
        throw new Error(problem.detail || t('Saving failed'));
      }
      window.location.assign(response.url);
    } catch (error) {
      saving = false; status.dataset.error = '1'; status.textContent = error.message || t('Saving failed'); refresh();
    }
  });
  window.addEventListener('beforeunload', event => {
    if (!saving && JSON.stringify(snapshot()) !== initialState) { event.preventDefault(); event.returnValue = ''; }
  });
  refresh();
})();
