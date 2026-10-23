import { useState } from "react";
import "./App.css";
import ChatScreen from "./ChatScreen.jsx";
import AdminScreen from "./AdminScreen.jsx";

// Dos pantallas nada más (ver CLAUDE.md: nada de router todavía, sería
// sobrekill para esto). El tab activo es estado simple de React.
const TABS = [
  { id: "chat", label: "Chat" },
  { id: "admin", label: "Cargar contenido" },
];

function App() {
  const [tabActivo, setTabActivo] = useState("chat");

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-header__brand">MiRob</span>
        <nav className="app-header__tabs" aria-label="Navegación principal">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`app-header__tab ${tabActivo === tab.id ? "is-active" : ""}`}
              aria-current={tabActivo === tab.id ? "page" : undefined}
              onClick={() => setTabActivo(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="app-main">
        {tabActivo === "chat" ? <ChatScreen /> : <AdminScreen />}
      </main>
    </div>
  );
}

export default App;
