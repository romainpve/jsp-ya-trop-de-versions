"use client"

import { useEffect, useRef, type ComponentPropsWithoutRef } from "react"
import { useInView, useMotionValue, useSpring } from "motion/react"

import { cn } from "@/lib/utils"

interface NumberTickerProps extends ComponentPropsWithoutRef<"span"> {
    value: number
    startValue?: number
    direction?: "up" | "down"
    delay?: number
    decimalPlaces?: number
}

export function NumberTicker({
    value,
    startValue = 0,
    direction = "up",
    delay = 0,
    className,
    decimalPlaces = 0,
    ...props
}: NumberTickerProps) {
    const ref = useRef<HTMLSpanElement>(null)
    const motionValue = useMotionValue(direction === "down" ? value : startValue)
    const springValue = useSpring(motionValue, {
        damping: 60,
        stiffness: 100,
    })
    const isInView = useInView(ref, { once: true, margin: "0px" })

    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | null = null

        if (isInView) {
            timer = setTimeout(() => {
                motionValue.set(direction === "down" ? startValue : value)
            }, delay * 1000)
        }

        return () => {
            if (timer !== null) {
                clearTimeout(timer)
            }
        }
    }, [motionValue, isInView, delay, value, direction, startValue])

    useEffect(
        () =>
            springValue.on("change", (latest) => {
                if (ref.current) {
                    ref.current.textContent = Intl.NumberFormat("en-US", {
                        minimumFractionDigits: decimalPlaces,
                        maximumFractionDigits: decimalPlaces,
                    }).format(Number(latest.toFixed(decimalPlaces)))
                }
            }),
        [springValue, decimalPlaces]
    )

    return (
        <span
            ref={ref}
            className={cn(
                "inline-block tracking-wider text-black tabular-nums dark:text-white",
                className
            )}
            {...props}
        >
            {startValue}
        </span>
    )
}

#TEXT HIGHLIGHTER
import { Highlighter } from "@/registry/magicui/highlighter"

export function HighlighterDemo() {
    return (
        <div className="text-center">
            <p className="leading-relaxed">
                The{" "}
                <Highlighter action="underline" color="#FF9800">
                    Magic UI Highlighter
                </Highlighter>{" "}
                makes important{" "}
                <Highlighter action="highlight" color="#87CEFA">
                    text stand out
                </Highlighter>{" "}
                effortlessly.
            </p>
        </div>
    )
}

#image gallery
'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { useInView } from 'framer-motion';
import { AspectRatio } from '@/components/ui/aspect-ratio';

export function ImageGallery() {
    return (
        <div className="relative flex min-h-screen w-full flex-col items-center justify-center py-10 px-4">
            <div className="mx-auto grid w-full max-w-5xl gap-6 sm:grid-cols-2 lg:grid-cols-3">

                {Array.from({ length: 3 }).map((_, col) => (
                    <div key={col} className="grid gap-6">
                        {Array.from({ length: 10 }).map((_, index) => {
                            const isPortrait = Math.random() > 0.5;
                            const width = isPortrait ? 1080 : 1920;
                            const height = isPortrait ? 1920 : 1080;
                            const ratio = isPortrait ? 9 / 16 : 16 / 9;

                            return (
                                <AnimatedImage
                                    key={`${col}-${index}`}
                                    alt={`Image ${col}-${index}`}
                                    src={`https://picsum.photos/seed/${col}-${index}/${width}/${height}`}
                                    ratio={ratio}
                                    placeholder={`https://placehold.co/${width}x${height}/`}
                                />
                            );
                        })}
                    </div>
                ))}
            </div>
        </div>
    );
}

interface AnimatedImageProps {
    alt: string;
    src: string;
    className?: string;
    placeholder?: string;
    ratio: number;
}

function AnimatedImage({ alt, src, ratio, placeholder }: AnimatedImageProps) {
    const ref = React.useRef(null);
    const isInView = useInView(ref, { once: true });
    const [isLoading, setIsLoading] = React.useState(true);

    const [imgSrc, setImgSrc] = React.useState(src);

    const handleError = () => {
        if (placeholder) {
            setImgSrc(placeholder);
        }
    };

    return (
        <AspectRatio
            ref={ref}
            ratio={ratio}
            className="bg-accent relative size-full rounded-lg border"
        >
            <img
                alt={alt}
                src={imgSrc}
                className={cn(
                    'size-full rounded-lg object-cover opacity-0 transition-all duration-1000 ease-in-out',
                    {
                        'opacity-100': isInView && !isLoading,
                    },
                )}
                onLoad={() => setIsLoading(false)}
                loading="lazy"
                onError={handleError}
            />
        </AspectRatio>
    );
}




#Before After Slider

import React, { useState, useRef, useCallback, useEffect } from 'react';

// This component takes two image URLs (before and after) and creates a slider to compare them.
export const ImageComparison = ({ beforeImage, afterImage, altBefore = 'Before', altAfter = 'After' }) => {
    // State to track the slider's position (from 0 to 100)
    const [sliderPosition, setSliderPosition] = useState(50);
    // State to track if the user is currently dragging the slider
    const [isDragging, setIsDragging] = useState(false);

    // Ref to the main container element to get its dimensions
    const containerRef = useRef(null);

    // Function to handle the slider movement (for both mouse and touch)
    const handleMove = useCallback((clientX) => {
        // If not dragging or no container ref, do nothing
        if (!isDragging || !containerRef.current) return;

        // Get the bounding box of the container
        const rect = containerRef.current.getBoundingClientRect();
        // Calculate the new slider position as a percentage
        let newPosition = ((clientX - rect.left) / rect.width) * 100;

        // Clamp the position to be between 0 and 100 to prevent it from going out of bounds
        newPosition = Math.max(0, Math.min(100, newPosition));

        setSliderPosition(newPosition);
    }, [isDragging]);

    // Mouse event handlers
    const handleMouseDown = () => setIsDragging(true);
    const handleMouseUp = () => setIsDragging(false);
    const handleMouseMove = (e) => handleMove(e.clientX);

    // Touch event handlers
    const handleTouchStart = () => setIsDragging(true);
    const handleTouchEnd = () => setIsDragging(false);
    const handleTouchMove = (e) => handleMove(e.touches[0].clientX);

    // Effect to add and clean up global event listeners for mouse up/leave
    // This ensures dragging stops even if the cursor leaves the component area
    useEffect(() => {
        window.addEventListener('mouseup', handleMouseUp);
        // Clean up the event listener when the component unmounts
        return () => {
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [handleMouseUp]);

    return (
        <div
            ref={containerRef}
            className="relative w-full max-w-4xl mx-auto select-none rounded-xl overflow-hidden shadow-2xl"
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseUp} // Stop dragging if mouse leaves the container
            onTouchMove={handleTouchMove}
            onTouchEnd={handleTouchEnd}
        >
            {/* After Image (Top Layer) - Its visibility is controlled by the clip-path */}
            <div
                className="absolute top-0 left-0 h-full w-full overflow-hidden"
                style={{ clipPath: `inset(0 ${100 - sliderPosition}% 0 0)` }}
            >
                <img
                    src={afterImage}
                    alt={altAfter}
                    className="h-full w-full object-cover object-left"
                    draggable="false"
                />
            </div>

            {/* Before Image (Bottom Layer) */}
            <img
                src={beforeImage}
                alt={altBefore}
                className="block h-full w-full object-cover object-left"
                draggable="false"
            />

            {/* Slider Handle */}
            <div
                className="absolute top-0 bottom-0 w-1.5 bg-white/80 cursor-ew-resize flex items-center justify-center"
                style={{ left: `calc(${sliderPosition}% - 0.375rem)` }} // Center the handle on the line
                onMouseDown={handleMouseDown}
                onTouchStart={handleTouchStart}
            >
                <div className={`bg-white rounded-full h-12 w-12 flex items-center justify-center shadow-md transition-all duration-200 ease-in-out ${isDragging ? 'scale-110 shadow-xl' : ''}`}>
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-700">
                        <line x1="15" y1="18" x2="9" y2="12"></line>
                        <line x1="9" y1="6" x2="15" y2="12"></line>
                    </svg>
                </div>
            </div>
        </div>
    );
};


#NAVIGATION MENUE FOR DESKTOP
'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { cva } from 'class-variance-authority';
import { ChevronDownIcon } from 'lucide-react';
import { NavigationMenu as NavigationMenuPrimitive } from 'radix-ui';

function NavigationMenu({
    className,
    children,
    viewport = true,
    ...props
}: React.ComponentProps<typeof NavigationMenuPrimitive.Root> & {
    viewport?: boolean;
}) {
    return (
        <NavigationMenuPrimitive.Root
            data-slot="navigation-menu"
            data-viewport={viewport}
            className={cn('group/navigation-menu relative flex max-w-max flex-1 items-center justify-center', className)}
            {...props}
        >
            {children}
            {viewport && <NavigationMenuViewport />}
        </NavigationMenuPrimitive.Root>
    );
}

function NavigationMenuList({ className, ...props }: React.ComponentProps<typeof NavigationMenuPrimitive.List>) {
    return (
        <NavigationMenuPrimitive.List
            data-slot="navigation-menu-list"
            className={cn('group flex flex-1 list-none items-center justify-center gap-1', className)}
            {...props}
        />
    );
}

function NavigationMenuItem({ className, ...props }: React.ComponentProps<typeof NavigationMenuPrimitive.Item>) {
    return (
        <NavigationMenuPrimitive.Item data-slot="navigation-menu-item" className={cn('relative', className)} {...props} />
    );
}

const navigationMenuTriggerStyle = cva(
    'cursor-pointer group inline-flex h-9 w-max items-center justify-center rounded-md bg-background px-4 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 data-[active=true]:bg-accent/50 data-[state=open]:bg-accent/50 data-[active=true]:text-accent-foreground ring-ring/10 dark:ring-ring/20 dark:outline-ring/40 outline-ring/50 transition-[color,box-shadow] focus-visible:ring-4 focus-visible:outline-1',
);

function NavigationMenuTrigger({
    className,
    children,
    ...props
}: React.ComponentProps<typeof NavigationMenuPrimitive.Trigger>) {
    return (
        <NavigationMenuPrimitive.Trigger
            data-slot="navigation-menu-trigger"
            className={cn(navigationMenuTriggerStyle(), 'group', className)}
            {...props}
        >
            {children}{' '}
            <ChevronDownIcon
                className="relative top-[1px] ms-1 size-3.5 opacity-60 transition duration-300 group-data-[state=open]:rotate-180"
                aria-hidden="true"
            />
        </NavigationMenuPrimitive.Trigger>
    );
}

function NavigationMenuContent({ className, ...props }: React.ComponentProps<typeof NavigationMenuPrimitive.Content>) {
    return (
        <NavigationMenuPrimitive.Content
            data-slot="navigation-menu-content"
            className={cn(
                'data-[motion^=from-]:animate-in data-[motion^=to-]:animate-out data-[motion^=from-]:fade-in data-[motion^=to-]:fade-out data-[motion=from-end]:slide-in-from-right-52! data-[motion=from-start]:slide-in-from-left-52! data-[motion=to-end]:slide-out-to-right-52! data-[motion=to-start]:slide-out-to-left-52! top-0 left-0 w-full p-2 pr-2.5 md:absolute md:w-auto',
                'group-data-[viewport=false]/navigation-menu:bg-popover group-data-[viewport=false]/navigation-menu:text-popover-foreground group-data-[viewport=false]/navigation-menu:data-[state=open]:animate-in group-data-[viewport=false]/navigation-menu:data-[state=closed]:animate-out group-data-[viewport=false]/navigation-menu:data-[state=closed]:zoom-out-95 group-data-[viewport=false]/navigation-menu:data-[state=open]:zoom-in-95 group-data-[viewport=false]/navigation-menu:data-[state=open]:fade-in-0 group-data-[viewport=false]/navigation-menu:data-[state=closed]:fade-out-0 group-data-[viewport=false]/navigation-menu:top-full group-data-[viewport=false]/navigation-menu:mt-1.5 group-data-[viewport=false]/navigation-menu:overflow-hidden group-data-[viewport=false]/navigation-menu:rounded-md group-data-[viewport=false]/navigation-menu:border group-data-[viewport=false]/navigation-menu:shadow group-data-[viewport=false]/navigation-menu:duration-200 **:data-[slot=navigation-menu-link]:focus:ring-0 **:data-[slot=navigation-menu-link]:focus:outline-none',
                className,
            )}
            {...props}
        />
    );
}

function NavigationMenuViewport({
    className,
    ...props
}: React.ComponentProps<typeof NavigationMenuPrimitive.Viewport>) {
    return (
        <div className={cn('absolute top-full left-0 isolate z-50 flex justify-center')}>
            <NavigationMenuPrimitive.Viewport
                data-slot="navigation-menu-viewport"
                className={cn(
                    'shadow-md shadow-black/5 rounded-md border border-border bg-popover text-popover-foreground p-1.5 origin-top-center data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-90 relative mt-1.5 h-[var(--radix-navigation-menu-viewport-height)] w-full overflow-hidden md:w-[var(--radix-navigation-menu-viewport-width)]',
                    className,
                )}
                {...props}
            />
        </div>
    );
}

function NavigationMenuLink({ className, ...props }: React.ComponentProps<typeof NavigationMenuPrimitive.Link>) {
    return (
        <NavigationMenuPrimitive.Link
            data-slot="navigation-menu-link"
            className={cn(
                'hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground data-[active=true]:bg-accent/50 data-[active=true]:text-accent-foreground ring-ring/10 dark:ring-ring/20 dark:outline-ring/40 outline-ring/50 flex flex-col gap-1 rounded-md p-2 text-sm transition-[color,box-shadow] focus-visible:ring-4 focus-visible:outline-1',
                className,
            )}
            {...props}
        />
    );
}

function NavigationMenuIndicator({
    className,
    ...props
}: React.ComponentProps<typeof NavigationMenuPrimitive.Indicator>) {
    return (
        <NavigationMenuPrimitive.Indicator
            data-slot="navigation-menu-indicator"
            className={cn(
                'data-[state=visible]:animate-in data-[state=hidden]:animate-out data-[state=hidden]:fade-out data-[state=visible]:fade-in top-full z-[1] flex h-1.5 items-end justify-center overflow-hidden',
                className,
            )}
            {...props}
        >
            <div className="bg-border relative top-[60%] h-2 w-2 rotate-45 rounded-ts-md shadow-md" />
        </NavigationMenuPrimitive.Indicator>
    );
}

export {
    NavigationMenu,
    NavigationMenuList,
    NavigationMenuItem,
    NavigationMenuContent,
    NavigationMenuTrigger,
    NavigationMenuLink,
    NavigationMenuIndicator,
    NavigationMenuViewport,
    navigationMenuTriggerStyle,
};


#testimonials columns
"use client";
import React from "react";
import { motion } from "motion/react";


export const TestimonialsColumn = (props: {
    className?: string;
    testimonials: typeof testimonials;
    duration?: number;
}) => {
    return (
        <div className={props.className}>
            <motion.div
                animate={{
                    translateY: "-50%",
                }}
                transition={{
                    duration: props.duration || 10,
                    repeat: Infinity,
                    ease: "linear",
                    repeatType: "loop",
                }}
                className="flex flex-col gap-6 pb-6 bg-background"
            >
                {[
                    ...new Array(2).fill(0).map((_, index) => (
                        <React.Fragment key={index}>
                            {props.testimonials.map(({ text, image, name, role }, i) => (
                                <div className="p-10 rounded-3xl border shadow-lg shadow-primary/10 max-w-xs w-full" key={i}>
                                    <div>{text}</div>
                                    <div className="flex items-center gap-2 mt-5">
                                        <img
                                            width={40}
                                            height={40}
                                            src={image}
                                            alt={name}
                                            className="h-10 w-10 rounded-full"
                                        />
                                        <div className="flex flex-col">
                                            <div className="font-medium tracking-tight leading-5">{name}</div>
                                            <div className="leading-5 opacity-60 tracking-tight">{role}</div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </React.Fragment>
                    )),
                ]}
            </motion.div>
        </div>
    );
};

;