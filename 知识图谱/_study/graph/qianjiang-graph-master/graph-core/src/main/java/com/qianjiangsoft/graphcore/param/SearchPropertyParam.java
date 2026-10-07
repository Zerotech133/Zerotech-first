package com.qianjiangsoft.graphcore.param;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;

/**
 * @author marshallwu
 * @date 2022/3/1 14:53
 */
@Data
@Builder
@AllArgsConstructor
public class SearchPropertyParam {
    String propertyKey;
    String propertyValue;
}
