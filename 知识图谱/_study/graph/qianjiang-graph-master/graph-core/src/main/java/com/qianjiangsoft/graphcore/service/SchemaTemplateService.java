package com.qianjiangsoft.graphcore.service;

import com.qianjiangsoft.graphcore.domain.entity.SchemaTemplate;

/**
 * @author marshallwu
 * @date 2022/3/18 10:55
 */
public interface SchemaTemplateService {

    /**
     * 
     * @param: id
     * @return: void
     * @author: marshall
     * @date: 2022/3/18
    */
    SchemaTemplate queryTemplateById(Integer id);
}
