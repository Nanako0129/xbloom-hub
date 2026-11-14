// hub.svelte.ts runs first: it takes the token out of the address before
// anything reads the route (docs/PLAN.md, S3c, C10).
import "./lib/hub.svelte";
import "./global.css";
import { mount } from "svelte";
import App from "./App.svelte";
import { language } from "./lib/i18n.svelte";

document.documentElement.lang = language.code;
mount(App, { target: document.getElementById("app")! });
