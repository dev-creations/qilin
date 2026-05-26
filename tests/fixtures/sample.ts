import { readFile } from "fs/promises";

export interface User {
  id: string;
  name: string;
}

export type UserOrNull = User | null;

export function greet(user: User): string {
  return `hello, ${user.name}`;
}

export class UserStore {
  private users: Map<string, User> = new Map();

  add(user: User): void {
    this.users.set(user.id, user);
  }

  get(id: string): UserOrNull {
    return this.users.get(id) ?? null;
  }

  remove(id: string): boolean {
    return this.users.delete(id);
  }
}
