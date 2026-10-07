package com.qianjiangsoft.graphcore.engine;

import com.qianjiangsoft.graphcore.model.Space;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/2/24 10:16
 */
public class CardParamContextHolder {

    private static final ThreadLocal<CardQueryParam> threadLocal = new ThreadLocal<>();

    public static CardQueryParam get(){
        return threadLocal.get();
    }


    public static void set(String key, List<String> valueList){
        CardQueryParam queryParam = new CardQueryParam();
        queryParam.setKey(key);
        queryParam.setValueList(valueList);
        threadLocal.set(queryParam);
    }

    public static void remove(){
        if(threadLocal.get() != null) {
            threadLocal.remove();
        }
    }
}
