chrome.runtime.onInstalled.addListener(function(details){
console.log("Installed")
 chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((error) => console.error(error));
});
