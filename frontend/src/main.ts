import "./lib/hub.svelte";
import "./global.css";
import { mount } from "svelte";
import App from "./App.svelte";
import { language } from "./lib/i18n.svelte";

document.documentElement.lang = language.code;
mount(App, { target: document.getElementById("app")! });
