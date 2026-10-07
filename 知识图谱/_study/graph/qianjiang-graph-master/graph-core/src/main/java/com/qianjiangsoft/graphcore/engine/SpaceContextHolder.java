package com.qianjiangsoft.graphcore.engine;

import com.qianjiangsoft.graphcore.model.Space;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 10:16
 */
public class SpaceContextHolder {
    private static final ThreadLocal<Space> threadLocal = new ThreadLocal<>();

    public static void set(Space space){
        threadLocal.set(space);
    }

    public static Space get(){
        return threadLocal.get();
    }

    public static String getSpaceId(){
        return get().getId();
    }

    public static void remove() {
            if(threadLocal.get() != null) {
                threadLocal.remove();
            }
    }
}
