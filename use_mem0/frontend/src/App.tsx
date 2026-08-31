import { useEffect, useState } from "react";
import { getMe, type User } from "./api";
import { Login } from "./Login";
import { Workspace } from "./Workspace";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    getMe().then((value) => {
      setUser(value);
      setChecked(true);
    });
  }, []);

  if (!checked) return null;
  return user ? <Workspace user={user} /> : <Login />;
}
