// CLI response headers inspect every tab. A suspended tab must not block the task.
exports.default = async ({ page }) => {
  const originalTitle = page.title.bind(page);
  let pending;
  page.title = async () => {
    if (!pending) {
      const lookup = Promise.resolve().then(originalTitle).catch(() => '(title unavailable)');
      pending = lookup;
      void lookup.then(() => { if (pending === lookup) pending = undefined; });
    }
    let timer;
    try {
      return await Promise.race([
        pending,
        new Promise(resolve => { timer = setTimeout(() => resolve('(title unavailable)'), 1500); })
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
};
