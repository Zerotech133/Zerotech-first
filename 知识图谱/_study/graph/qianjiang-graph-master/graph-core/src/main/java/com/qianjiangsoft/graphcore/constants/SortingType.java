package com.qianjiangsoft.graphcore.constants;

/**
 * @author marshallwu
 * @date 2022/4/29 13:54
 */
public enum SortingType {

    count("次数"), strength("强度"), property("属性"),
    outDegree("出度"), inDegree("入度"), allDegree("总度数");

    public final String name;

    SortingType(String name) {
        this.name = name;
    }
}
