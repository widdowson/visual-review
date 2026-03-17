// Service worker: handles extension reload triggered by #vr_reload hash
chrome.runtime.onMessage.addListener(function(msg) {
  if (msg && msg.type === 'reload') {
    chrome.runtime.reload();
  }
});
