// Setup page: show the fields of the chosen source and destination, and browse local folders.
// Job page: follow a running export.
(() => {
  document.querySelectorAll('[data-kind-group]').forEach(group => {
    const radios = [...group.querySelectorAll(`input[name="${group.dataset.kindGroup}"]`)];
    const update = () => {
      const chosen = (radios.find(radio => radio.checked) || {}).value;
      group.querySelectorAll('[data-kind]').forEach(part => {
        const active = part.dataset.kind === chosen;
        part.hidden = !active;
        // Hidden fields are left out of the form, so a stale choice on the other side is not sent.
        part.querySelectorAll('input, select').forEach(field => { field.disabled = !active; });
      });
    };
    radios.forEach(radio => radio.addEventListener('change', update));
    update();
  });

  document.querySelectorAll('[data-folder-picker]').forEach(picker => {
    const input = document.getElementById(picker.dataset.folderPicker);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'secondary';
    toggle.textContent = 'Selaa kansioita';
    const panel = document.createElement('div');
    panel.className = 'folder-panel';
    panel.hidden = true;
    picker.append(toggle, panel);

    const show = (path) => fetch(`/api/folders?path=${encodeURIComponent(path)}`)
      .then(response => response.ok ? response.json() : response.json().then(body => Promise.reject(body.detail)))
      .then(data => {
        panel.replaceChildren();
        const head = document.createElement('p');
        head.innerHTML = '<strong></strong> <span class="hint"></span>';
        head.querySelector('strong').textContent = data.path;
        head.querySelector('span').textContent = `${data.images} kuvaa`;
        const choose = document.createElement('button');
        choose.type = 'button';
        choose.textContent = 'Valitse tämä kansio';
        choose.addEventListener('click', () => { input.value = data.path; panel.hidden = true; });
        const list = document.createElement('ul');
        const entry = (label, target) => {
          const item = document.createElement('li');
          const link = document.createElement('button');
          link.type = 'button';
          link.className = 'folder-link';
          link.textContent = label;
          link.addEventListener('click', () => show(target));
          item.append(link);
          list.append(item);
        };
        if (data.parent) entry('↑ Yläkansio', data.parent);
        data.dirs.forEach(name => entry(`📁 ${name}`, `${data.path.replace(/\/$/, '')}/${name}`));
        if (data.roots.length > 1) data.roots.forEach(root => entry(`⌂ ${root}`, root));
        panel.append(head, choose, list);
      })
      .catch(detail => { panel.textContent = typeof detail === 'string' ? detail : 'Kansiota ei voitu avata'; });

    toggle.addEventListener('click', () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) show(input.value);
    });
  });

  const exportSection = document.querySelector('[data-export-url]');
  if (exportSection && exportSection.dataset.exportState === 'running') {
    const status = document.getElementById('export-status');
    const poll = () => fetch(exportSection.dataset.exportUrl).then(response => response.json()).then(state => {
      if (state.state === 'running') {
        status.textContent = `Vienti käynnissä: ${state.done} / ${state.total}`;
        setTimeout(poll, 1500);
      } else {
        window.location.reload();
      }
    }, () => setTimeout(poll, 5000));
    poll();
  }
})();
