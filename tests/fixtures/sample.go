package main

import (
	"fmt"
	"strings"
)

type Counter struct {
	value int
}

func NewCounter(start int) *Counter {
	return &Counter{value: start}
}

func (c *Counter) Increment() {
	c.value++
}

func greet(name string) string {
	return fmt.Sprintf("hello, %s", strings.ToLower(name))
}

func main() {
	c := NewCounter(0)
	c.Increment()
	fmt.Println(greet("World"), c.value)
}
