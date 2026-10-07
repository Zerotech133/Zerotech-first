package com.qianjiangsoft.graphcore.constants;

import com.fasterxml.jackson.annotation.JsonValue;
import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author ulmarr8861
 * @date 2022/4/24
 *   查找路径，搜索类型：全路径/最短路径/非循环路径
 */
@ToString
@AllArgsConstructor
public enum SearchPathType {
    all("all"), shortest("shortest");

    public final String lable;
    }
