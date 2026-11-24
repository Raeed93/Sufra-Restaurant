import { useEffect, useState } from "react";
import { api, type Preference, type User } from "./api";
import Chat from "./Chat";
import Login from "./Login";

export default function App() {
  // undefined = still checking the cookie, null = logged out
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [prefs, setPrefs] = useState<Preference[]>([]);

  useEffect(() => {
    api
      .me()
      .then((data) => {
        setUser(data.user);
        setPrefs(data.preferences);
      })
      .catch(() => setUser(null));
  }, []);

  if (user === undefined) return <div className="boot" aria-busy="true" />;

  if (user === null) {
    return (
      <Login
        onLoggedIn={async () => {
          const data = await api.me();
          setPrefs(data.preferences);
          setUser(data.user);
        }}
      />
    );
  }

  return (
    <Chat
      user={user}
      prefs={prefs}
      setPrefs={setPrefs}
      onLogout={async () => {
        await api.logout();
        setPrefs([]);
        setUser(null);
      }}
    />
  );
}
