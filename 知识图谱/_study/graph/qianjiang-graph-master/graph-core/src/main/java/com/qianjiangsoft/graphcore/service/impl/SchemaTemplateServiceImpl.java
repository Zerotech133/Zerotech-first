package com.qianjiangsoft.graphcore.service.impl;

import com.qianjiangsoft.graphcore.domain.entity.SchemaTemplate;
import com.qianjiangsoft.graphcore.mapper.SchemaTemplateMapper;
import com.qianjiangsoft.graphcore.service.SchemaTemplateService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

/**
 * @author marshallwu
 * @date 2022/3/18 10:57
 */
@Service
public class SchemaTemplateServiceImpl implements SchemaTemplateService {

    @Autowired
    SchemaTemplateMapper schemaTemplateMapper;

    /**
     * @param id
     * @param: id
     * @return: void
     * @author: marshall
     * @date: 2022/3/18
     */
    @Override
    public SchemaTemplate queryTemplateById(Integer id) {
        return schemaTemplateMapper.selectById(id);
    }
}
