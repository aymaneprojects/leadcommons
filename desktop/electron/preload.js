"use strict";
// The pages get no access to Node or the file system; they only learn that they run in the app.
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("leadcommonsDesktop", { isDesktop: true, platform: process.platform });
