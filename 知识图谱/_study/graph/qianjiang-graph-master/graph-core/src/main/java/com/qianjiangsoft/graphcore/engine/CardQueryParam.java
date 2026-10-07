package com.qianjiangsoft.graphcore.engine;

import lombok.Data;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/12/12 17:19
 */
@Data
public class CardQueryParam {
    private String key;
    private List<String> valueList;
}
