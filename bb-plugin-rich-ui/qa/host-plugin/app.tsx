import { definePluginApp } from "@get-bb/plugin-sdk/app";
import { RichContent } from "../../rich";
import "../../app.css";
export default definePluginApp((app) => {
  app.slots.experimental_codeFenceRenderer({
    id: "literal-qa",
    languages: ["chatgpt-rich"],
    component: RichContent,
  });
});
