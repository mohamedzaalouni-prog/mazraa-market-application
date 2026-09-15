(() => {
    if (window.__accountMenuInitialized) {
        return;
    }

    window.__accountMenuInitialized = true;

    const menus = document.querySelectorAll("[data-account-menu]");

    if (!menus.length) {
        return;
    }

    const menuItems = Array.from(menus).map((menu, index) => {
        const trigger = menu.querySelector("[data-account-trigger]");
        const dropdown = menu.querySelector("[data-account-dropdown]");

        if (!trigger || !dropdown) {
            return null;
        }

        if (!trigger.id) {
            trigger.id = `account-menu-trigger-${index + 1}`;
        }

        if (!dropdown.id) {
            dropdown.id = `account-menu-panel-${index + 1}`;
        }

        trigger.setAttribute("aria-controls", dropdown.id);
        trigger.setAttribute("aria-expanded", "false");
        dropdown.setAttribute("aria-labelledby", trigger.id);
        dropdown.hidden = true;
        menu.classList.remove("is-open");
        menu.classList.remove("is-closing");

        return { menu, trigger, dropdown, closeTimer: null };
    }).filter(Boolean);

    if (!menuItems.length) {
        return;
    }

    let openItem = null;

    const getTransitionDurationMs = (element) => {
        const computed = window.getComputedStyle(element);
        const durations = computed.transitionDuration.split(",");
        const delays = computed.transitionDelay.split(",");

        const toMs = (timeValue) => {
            const value = timeValue.trim();
            if (value.endsWith("ms")) {
                return parseFloat(value);
            }
            return parseFloat(value) * 1000;
        };

        const durationValues = durations.map(toMs);
        const delayValues = delays.map(toMs);
        const maxLength = Math.max(durationValues.length, delayValues.length);

        let total = 0;
        for (let index = 0; index < maxLength; index += 1) {
            const duration = durationValues[index] ?? durationValues[durationValues.length - 1] ?? 0;
            const delay = delayValues[index] ?? delayValues[delayValues.length - 1] ?? 0;
            total = Math.max(total, duration + delay);
        }

        return total;
    };

    const isReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const finishClose = (item) => {
        if (!item) {
            return;
        }
        item.menu.classList.remove("is-closing");
        item.dropdown.hidden = true;
        item.closeTimer = null;
    };

    const closeMenu = (item, { restoreFocus = false, immediate = false } = {}) => {
        if (!item) {
            return;
        }

        if (item.closeTimer) {
            window.clearTimeout(item.closeTimer);
            item.closeTimer = null;
        }

        item.menu.classList.remove("is-open");
        item.trigger.setAttribute("aria-expanded", "false");

        if (immediate || isReducedMotion) {
            finishClose(item);
        } else {
            item.menu.classList.add("is-closing");
            const duration = getTransitionDurationMs(item.dropdown) || 180;
            item.closeTimer = window.setTimeout(() => {
                if (!item.menu.classList.contains("is-open")) {
                    finishClose(item);
                }
            }, duration + 20);
        }

        if (openItem === item) {
            openItem = null;
        }

        if (restoreFocus) {
            item.trigger.focus();
        }
    };

    const openMenu = (item) => {
        if (!item) {
            return;
        }

        if (openItem && openItem !== item) {
            closeMenu(openItem, { immediate: true });
        }

        if (item.closeTimer) {
            window.clearTimeout(item.closeTimer);
            item.closeTimer = null;
        }

        item.menu.classList.remove("is-closing");
        item.dropdown.hidden = false;
        window.requestAnimationFrame(() => {
            item.menu.classList.add("is-open");
            item.trigger.setAttribute("aria-expanded", "true");
            openItem = item;
        });
    };

    const toggleMenu = (item) => {
        if (openItem === item) {
            closeMenu(item);
            return;
        }

        openMenu(item);
    };

    menuItems.forEach((item) => {
        item.trigger.addEventListener("click", (event) => {
            event.preventDefault();
            toggleMenu(item);
        });

        item.dropdown.addEventListener("click", (event) => {
            const actionElement = event.target.closest("a, button");
            if (actionElement) {
                closeMenu(item);
            }
        });
    });

    document.addEventListener("pointerdown", (event) => {
        if (!openItem) {
            return;
        }

        if (!openItem.menu.contains(event.target)) {
            closeMenu(openItem);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && openItem) {
            closeMenu(openItem, { restoreFocus: true });
        }
    });

    document.addEventListener("focusin", (event) => {
        if (!openItem) {
            return;
        }

        if (!openItem.menu.contains(event.target)) {
            closeMenu(openItem);
        }
    });
})();
