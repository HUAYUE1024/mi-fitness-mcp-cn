(() => {
  const stored = localStorage.getItem('mi_fitness_theme');
  const light = matchMedia('(prefers-color-scheme: light)').matches;
  document.documentElement.dataset.theme = stored || (light ? 'light' : 'dark');
})();
