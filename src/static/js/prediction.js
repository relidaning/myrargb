(() => {
  const form = document.querySelector('#prediction-form');
  const input = document.querySelector('#prediction-text');
  const submit = document.querySelector('#prediction-submit');
  const status = document.querySelector('#prediction-status');
  const error = document.querySelector('#prediction-error');
  const output = document.querySelector('#prediction-output');
  const region = document.querySelector('.prediction-results');
  let pending = false;
  const count = () => { document.querySelector('#char-count').textContent = `${input.value.length} / ${input.maxLength}`; };
  input.addEventListener('input', count);
  input.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      if (!pending) form.requestSubmit();
    }
  });
  document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => {
    input.value = button.dataset.example;
    count();
    input.focus();
  }));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending) return;
    error.hidden = true;
    const text = input.value.trim();
    const models = [...form.querySelectorAll('input[name="models"]:checked')].map(box => box.value);
    if (!text || !models.length) {
      error.textContent = !text ? 'Enter a filename or some text.' : 'Select at least one model.';
      error.hidden = false;
      return;
    }
    pending = true;
    submit.disabled = true;
    submit.textContent = 'Predicting…';
    region.setAttribute('aria-busy', 'true');
    output.replaceChildren();
    status.textContent = 'Predicting… The first request may take longer.';
    try {
      const response = await fetch('/model/predict', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, models})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Prediction failed. Please try again.');
      for (const result of data.results) {
        const article = document.createElement('article');
        article.className = 'prediction-result';
        const label = document.createElement('h3');
        label.textContent = result.label;
        const title = document.createElement('p');
        title.className = 'prediction-title';
        title.textContent = result.title || 'No title generated';
        const timing = document.createElement('p');
        timing.className = 'muted';
        timing.textContent = `${(result.duration_ms / 1000).toFixed(2)} s`;
        article.append(label, title, timing);
        output.append(article);
      }
      status.textContent = `${data.results.length} ${data.results.length === 1 ? 'model tested' : 'models tested'}.`;
    } catch (failure) {
      status.textContent = 'No prediction returned.';
      error.textContent = failure instanceof TypeError ? 'Could not reach the app. Check your connection and try again.' : failure.message;
      error.hidden = false;
    } finally {
      pending = false;
      submit.disabled = false;
      submit.textContent = 'Predict title';
      region.setAttribute('aria-busy', 'false');
    }
  });
})();
