package com.qianjiangsoft.graphcore.helper;

import cn.hutool.core.util.StrUtil;

/**
 * @author marshallwu
 * @date 2022/12/12 12:13
 */
public class CardHelper {

    public static String getCardAdjacencyTableKey(String cardKey, String cardValue){
        return StrUtil.format("{}_{}", cardKey, cardValue);
    }
}
