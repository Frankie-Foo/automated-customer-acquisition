import { useEffect } from "react";
import legacyMarkup from "./legacyMarkup.html?raw";
import "./legacy-styles.css";

export default function App() {
  useEffect(() => {
    let mounted = true;
    import("./legacy-controller.js").catch((error) => {
      if (!mounted) return;
      console.error("Failed to load dashboard controller", error);
    });
    return () => {
      mounted = false;
    };
  }, []);

  return <div dangerouslySetInnerHTML={{ __html: legacyMarkup }} />;
}
